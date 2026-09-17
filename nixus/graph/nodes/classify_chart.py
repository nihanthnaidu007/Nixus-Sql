import json
import re
from datetime import datetime

import pandas as pd

from nixus.config import settings
from nixus.graph.state import SQLAgentState

try:
    import numpy as np
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


def _is_numeric(val) -> bool:
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


# The generator is told to "Always add LIMIT 1000 unless the user specifies a limit"
# (see generate_sql.py), so an explicit LIMIT *below* that safety cap is the tell-tale
# of a user-requested top-/bottom-N subset rather than a whole-set composition.
_DEFAULT_SAFETY_LIMIT = 1000
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)", re.IGNORECASE)
_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


_SHARE_KEYWORDS = ("share", "percent", "pct", "proportion", "fraction", "ratio")


def _looks_like_share(col: str) -> bool:
    """True when a numeric column is a proportion of a whole (share/percent/ratio).

    Such a column is a COMPOSITION signal, not an independent measure: a scatter of a
    quantity against its own share is a meaningless straight line. Excluding it from
    the scatter trigger keeps "share of revenue by plan" a composition (pie/bar)
    rather than a misleading scatter, while genuine two-measure relationships (e.g.
    revenue vs user_count) are untouched.
    """
    c = col.lower()
    return any(kw in c for kw in _SHARE_KEYWORDS)


def _is_ranking(sql: str) -> bool:
    """True when the SQL is a top-/bottom-N RANKING (keep bar), not a composition (pie).

    A ranking ("top 5 orgs by users") orders by a measure *and* caps the rows with an
    explicit small LIMIT — the user asked for a subset. A composition ("share of
    revenue by plan") returns the whole set. Descending order alone is far too weak a
    signal (the generator sorts nearly everything DESC), so the discriminator is the
    explicit LIMIT *below* the default safety cap of 1000, which is only emitted for a
    genuine N-item request; the ORDER BY confirms it is a ranked selection. Direction
    is intentionally ignored so a "bottom N" list also stays an honest bar.
    """
    if not sql:
        return False
    m = _LIMIT_RE.search(sql)
    if not m or int(m.group(1)) >= _DEFAULT_SAFETY_LIMIT:
        return False
    return bool(_ORDER_BY_RE.search(sql))


def _multi_series_col(df, candidates, x_col, cap: int):
    """The categorical that splits a line into one series per value, or None.

    A genuine SERIES dimension (e.g. `tier` in month×tier×revenue): it has between
    2 and ``cap`` distinct values — few enough to read as separate lines — AND the
    x-axis genuinely repeats across them (more rows than distinct x), which is the
    hallmark of a split rather than a per-row label. Above the cap we return None so
    the caller falls back to a single line: a 40-line chart is noise, worse than one.
    """
    if df[x_col].nunique() >= len(df):
        # x is unique per row → there is no split to draw, just labels.
        return None
    for col in candidates:
        if col == x_col:
            continue
        k = df[col].nunique()
        if 2 <= k <= cap:
            return col
    return None


def _grouped_bar_cols(df, categorical_cols, cap: int):
    """Pick (axis, series) for a grouped bar from two categoricals, or (None, None).

    With two category dimensions and one measure (e.g. users by country × tier), the
    SERIES is the lower-cardinality category — capped at ``cap`` so the legend stays
    legible — and the AXIS is the other (the groups along x). Above the cap there is
    no readable split, so we return (None, None) and the caller draws a single bar.
    """
    if len(categorical_cols) < 2:
        return None, None
    by_card = sorted(categorical_cols, key=lambda c: df[c].nunique())
    series_col, axis_col = by_card[0], by_card[-1]
    k = df[series_col].nunique()
    if 2 <= k <= cap and df[axis_col].nunique() >= 2:
        return axis_col, series_col
    return None, None


def _fig_to_json(fig) -> str:
    """Serialize a Plotly figure to JSON without typed binary arrays.

    Plotly's default to_json() may emit {"dtype":"i2","bdata":"..."} for
    integer columns when orjson is installed. Standard json.dumps avoids
    that entirely; numpy scalars/arrays are converted to plain Python types.
    """
    def _make_serializable(obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, dict):
            return {k: _make_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_make_serializable(v) for v in obj]
        return obj

    return json.dumps(_make_serializable(fig.to_dict()), default=str)


async def classify_chart_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "classify_chart"
    if state.get("served_from_cache") and state.get("cache_result"):
        cr = state["cache_result"]
        rows = cr.get("result_preview") or []
        columns = list(rows[0].keys()) if rows else []
        # The cache stores only a 5-row preview but the true total separately; the
        # pie cap must use the true total so a large result served from cache is not
        # mistaken for a small-N composition.
        total_rows = cr.get("row_count") or len(rows)
    else:
        result = state.get("execution_result") or {}
        rows = result.get("rows", [])
        columns = result.get("columns", [])
        total_rows = result.get("row_count") or len(rows)

    if not rows:
        reason = "No rows to visualize" if not columns else "No rows returned — chart needs at least one row"
        state["chart_config"] = {
            "chart_type": "none", "title": "", "x_column": None, "y_column": None,
            "color_column": None, "reasoning": reason, "plotly_json": None,
        }
        state["completed_nodes"].append("classify_chart")
        state["stream_updates"].append({
            "timestamp": now(), "node": "classify_chart",
            "message": f"No chart — {reason}", "status": "done",
        })
        return state

    if len(rows) < 2 or not HAS_PLOTLY:
        state["chart_config"] = {
            "chart_type": "none", "title": "", "x_column": None, "y_column": None,
            "color_column": None, "reasoning": "Insufficient data for chart", "plotly_json": None,
        }
        state["completed_nodes"].append("classify_chart")
        state["stream_updates"].append({
            "timestamp": now(), "node": "classify_chart",
            "message": "No chart — insufficient data", "status": "done",
        })
        return state

    df = pd.DataFrame(rows[:500])

    date_cols, numeric_cols, categorical_cols = [], [], []
    for col in df.columns:
        col_lower = col.lower()
        if any(kw in col_lower for kw in [
            "date", "time", "year", "month", "day",
            "created", "updated", "timestamp",
        ]):
            date_cols.append(col)
            continue
        sample = df[col].dropna().head(20)
        if len(sample) == 0:
            categorical_cols.append(col)
            continue
        ratio = sum(_is_numeric(v) for v in sample) / len(sample)
        if ratio >= 0.8:
            numeric_cols.append(col)
        else:
            categorical_cols.append(col)

    # Cast identified numeric columns to float so plotly to_json() doesn't emit typed
    # binary arrays (dtype/bdata dicts) that pio.from_json() can't deserialize.
    for col in numeric_cols:
        try:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        except Exception:
            pass

    chart_type, x_col, y_col, color_col, reasoning = "none", None, None, None, ""

    # The SQL actually executed (fresh path) or replayed from cache. Used below to
    # tell a top-N RANKING apart from a parts-of-a-whole COMPOSITION.
    executed_sql = (state.get("validation_result") or {}).get("normalized_sql") \
        or state.get("generated_sql") or ""

    PIE_MAX_SLICES = settings.pie_max_slices
    MULTI_SERIES_MAX = settings.multi_series_max

    # A scatter needs two INDEPENDENT measures that genuinely relate. A share/percent
    # column is a part-of-a-whole, derived from another measure — so it never counts
    # toward the two-measure scatter trigger; it routes to the composition logic.
    share_cols = [c for c in numeric_cols if _looks_like_share(c)]
    measure_cols = [c for c in numeric_cols if c not in share_cols]

    if date_cols and numeric_cols:
        # 1) Time-series wins over everything: a date + a measure is a line.
        chart_type = "line"
        x_col = date_cols[0]
        y_col = numeric_cols[0]
        reasoning = f"Time-series: {x_col} × {y_col}"
        # MULTI-SERIES: a SECOND categorical (e.g. tier in month×tier×revenue) splits
        # the line into one series per value. The date stays the x-axis (it is the
        # ordered dimension); the categorical becomes the color/series — but only when
        # the series count is small enough to read (else fall back to one line).
        series_col = _multi_series_col(df, categorical_cols, x_col, MULTI_SERIES_MAX)
        if series_col is not None:
            color_col = series_col
            k = df[series_col].nunique()
            reasoning = f"Multi-line: {y_col} by {x_col} across {k} {series_col}"
    elif len(measure_cols) >= 2:
        # 2) Two (non-date) measures relating to each other -> scatter. An optional
        #    entity label becomes the color/hover, NOT a reason to fall back to bar.
        #    (A single measure per category is handled below — that is a bar/pie.)
        chart_type = "scatter"
        x_col = measure_cols[0]
        y_col = measure_cols[1]
        color_col = categorical_cols[0] if categorical_cols else None
        if color_col:
            n_entities = df[color_col].nunique()
            reasoning = f"Scatter: {y_col} vs {x_col} across {n_entities} {color_col}"
        else:
            reasoning = f"Scatter: {y_col} vs {x_col}"
    elif categorical_cols and numeric_cols:
        # 3) One category dimension + a single measure (or a measure and its share):
        #    either a small-N COMPOSITION (pie) or — for top-N rankings and large-N —
        #    an honest bar. Prefer the share column as the charted value when present
        #    (it is literally the parts-of-a-whole).
        row_count = total_rows
        cat_col = categorical_cols[0]
        num_col = share_cols[0] if share_cols else measure_cols[0]
        unique_cat = df[cat_col].nunique()

        # MULTI-SERIES (grouped bar): TWO category dimensions + one measure (and not a
        # share/composition) → grouped bars, one series per value of the small-N
        # second category, grouped along the other category's x-axis. Above the cap
        # there is no readable split, so this returns None and we fall through to the
        # single-category pie/bar logic below.
        gb_axis, gb_series = (None, None)
        if not share_cols:
            gb_axis, gb_series = _grouped_bar_cols(df, categorical_cols, MULTI_SERIES_MAX)

        if gb_series is not None:
            chart_type = "bar"
            x_col = gb_axis
            y_col = num_col
            color_col = gb_series
            k = df[gb_series].nunique()
            reasoning = f"Grouped bar: {num_col} by {gb_axis} across {k} {gb_series}"
        else:
            # RANKING (keep bar): a "top/most/highest N" query — ORDER BY a measure
            # with an explicit small LIMIT. Detected from the SQL, the strongest signal.
            is_ranking = _is_ranking(executed_sql)

            # COMPOSITION (pie): a clean parts-of-a-whole — one non-negative value per
            # category, few enough slices to read, and NOT a top-N ranking.
            is_composition = (
                not is_ranking
                and len(categorical_cols) == 1
                and unique_cat <= PIE_MAX_SLICES
                and row_count <= PIE_MAX_SLICES
                and unique_cat == row_count
                and df[num_col].min() >= 0
            )

            x_col = cat_col
            y_col = num_col
            if is_composition:
                chart_type = "pie"
                reasoning = f"Pie: composition of {cat_col} by {num_col} ({row_count} parts)"
            elif is_ranking:
                chart_type = "bar"
                reasoning = f"Bar: ranking by {num_col} (top {row_count})"
            else:
                chart_type = "bar"
                reasoning = f"Bar: {cat_col} × {num_col} ({row_count} rows)"

    plotly_json = None
    if chart_type != "none":
        try:
            if chart_type == "pie":
                kw = {"names": x_col, "values": y_col}
            else:
                kw = {"x": x_col, "y": y_col}
            if color_col and color_col in df.columns and chart_type != "pie":
                kw["color"] = color_col
            fig = getattr(px, chart_type)(df, **kw, title=reasoning)
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font={"color": "#e0f4ff", "family": "Outfit"},
                title_font={"color": "#00d4ff", "size": 14},
                legend={"bgcolor": "rgba(0,0,0,0)", "bordercolor": "rgba(0,212,255,0.2)"},
                xaxis={"gridcolor": "rgba(0,212,255,0.08)", "linecolor": "rgba(0,212,255,0.2)", "tickfont": {"color": "#7ba3c0"}},
                yaxis={"gridcolor": "rgba(0,212,255,0.08)", "linecolor": "rgba(0,212,255,0.2)", "tickfont": {"color": "#7ba3c0"}},
                margin={"l": 20, "r": 20, "t": 40, "b": 20},
            )
            # A single accent colour is only forced for SINGLE-series line/bar; when
            # color_col splits the figure into multiple series, plotly's own per-series
            # palette must stand so the series stay distinguishable.
            if chart_type == "bar" and not color_col:
                fig.update_traces(marker_color="#00d4ff", marker_line_color="rgba(0,212,255,0.3)", marker_line_width=1)
            elif chart_type == "line" and not color_col:
                fig.update_traces(line_color="#00d4ff", line_width=2)
            elif chart_type == "scatter":
                fig.update_traces(marker_color="#7c3aed", marker_size=8)
            plotly_json = _fig_to_json(fig)
        except Exception as e:
            chart_type = "none"
            reasoning = f"Chart failed: {e}"
            state["stream_updates"].append({
                "timestamp": now(), "node": "classify_chart",
                "message": f"Chart generation error: {e}",
                "status": "error",
            })

    state["chart_config"] = {
        "chart_type": chart_type, "x_column": x_col, "y_column": y_col,
        "color_column": color_col, "title": reasoning, "reasoning": reasoning,
        "plotly_json": plotly_json,
    }
    state["completed_nodes"].append("classify_chart")
    state["stream_updates"].append({
        "timestamp": now(), "node": "classify_chart",
        "message": f"Chart: {chart_type.upper()} — {reasoning}",
        "status": "done",
    })
    return state
