"""
투자 포트폴리오 대시보드 (경량·안정 버전)
설계 원칙: 백그라운드 스레드 없음, 모든 캐시 개수 제한 → 메모리 안정
"""
import streamlit as st
import requests
import json
import math
import os

st.set_page_config(page_title="내 포트폴리오", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
div[data-testid="stButton"] > button {
    border-radius: 10px; border: 1px solid #2a2a2a;
    background: linear-gradient(180deg, #1c1c1c, #151515);
    color: #e0e0e0; padding: 11px 16px; font-weight: 700; font-size: 14px;
    transition: all 0.15s ease;
}
div[data-testid="stButton"] > button:hover {
    border-color: #4dd2ff; color: #ffffff;
    background: linear-gradient(180deg, #1e2a30, #16222a);
}
div[data-testid="stCheckbox"] label p, div[data-testid="stToggle"] label p {
    font-size: 14px !important; font-weight: 700 !important;
}
@media (max-width: 900px) {
    div[data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; row-gap: 8px; }
}
</style>
""", unsafe_allow_html=True)

FINNHUB_SYMBOLS = {
    "QQQ": "QQQ", "VOO": "VOO", "SOXX": "SOXX", "SPY": "SPY", "SHY": "SHY",
    "UUP": "UUP", "TLT": "TLT",
}

PORTFOLIO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolios_data.json")


# ========== 구글 시트 저장소 ==========
@st.cache_resource
def _get_gsheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        sa_info = st.secrets.get("gcp_service_account")
        sheet_id = st.secrets.get("gsheet_id")
        if not sa_info or not sheet_id:
            return None
        creds = Credentials.from_service_account_info(
            dict(sa_info), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds).open_by_key(sheet_id)
    except Exception:
        return None


def _gsheet_read(tab):
    try:
        sh = _get_gsheet()
        if not sh:
            return None
        try:
            ws = sh.worksheet(tab)
        except Exception:
            return None
        raw = ws.acell("A1").value
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _gsheet_write(tab, data):
    try:
        sh = _get_gsheet()
        if not sh:
            return False
        try:
            ws = sh.worksheet(tab)
        except Exception:
            ws = sh.add_worksheet(title=tab, rows=1, cols=1)
        ws.update_acell("A1", json.dumps(data, ensure_ascii=False))
        return True
    except Exception:
        return False


def load_portfolios():
    gs = _gsheet_read("portfolios")
    if gs is not None:
        return gs
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_portfolios():
    data = st.session_state.portfolios
    _gsheet_write("portfolios", data)
    try:
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ========== 데이터 조회 (캐시+개수제한, 스레드 없음) ==========
def _secret(key):
    try:
        return st.secrets.get(key)
    except Exception:
        return None


@st.cache_data(ttl=60, max_entries=60)
def get_finnhub_quote(ticker):
    api_key = _secret("FINNHUB_API_KEY")
    if not api_key:
        return None
    sym = FINNHUB_SYMBOLS.get(ticker, ticker)
    try:
        res = requests.get("https://finnhub.io/api/v1/quote",
                           params={"symbol": sym, "token": api_key}, timeout=5)
        res.raise_for_status()
        d = res.json()
        if d.get("c") and d["c"] > 0:
            return {"current": d["c"], "change_pct": d.get("dp") or 0.0,
                    "high": d.get("h"), "low": d.get("l")}
    except Exception:
        pass
    return None


@st.cache_data(ttl=120, max_entries=40)
def get_naver_domestic_price(ticker):
    code = ticker.split(".")[0]
    try:
        from bs4 import BeautifulSoup
        res = requests.get(f"https://finance.naver.com/item/main.naver?code={code}",
                           headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        tag = soup.select_one("p.no_today span.blind")
        if tag:
            return float(tag.text.replace(",", ""))
    except Exception:
        pass
    return None


@st.cache_data(ttl=120, max_entries=20)
def get_naver_index(code):
    try:
        import re
        from bs4 import BeautifulSoup
        res = requests.get(f"https://finance.naver.com/sise/sise_index.naver?code={code}",
                           headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        now = soup.select_one("#now_value")
        rate = soup.select_one("#change_value_and_rate")
        if now:
            cur = float(now.text.replace(",", ""))
            pct = 0.0
            if rate:
                txt = rate.text.replace(",", "")
                m = re.search(r"([-+]?\d+\.\d+)", txt)
                if m:
                    pct = float(m.group(1))
                    if "하락" in txt:
                        pct = -abs(pct)
            return {"current": cur, "change_pct": pct}
    except Exception:
        pass
    return None


@st.cache_data(ttl=120, max_entries=10)
def get_usd_krw():
    try:
        res = requests.get("https://api.stock.naver.com/marketindex/exchange/FX_USDKRW",
                           headers={"User-Agent": "Mozilla/5.0",
                                    "Referer": "https://m.stock.naver.com/"}, timeout=5)
        d = res.json()
        price = d.get("closePrice") or d.get("calcPrice")
        if price:
            return float(str(price).replace(",", ""))
    except Exception:
        pass
    q = get_finnhub_quote("OANDA:USD_KRW")
    return q["current"] if q else 1380.0


@st.cache_data(ttl=300, max_entries=20)
def fetch_fred_series(series_id):
    api_key = _secret("FRED_API_KEY")
    if not api_key:
        return None
    try:
        res = requests.get("https://api.stlouisfed.org/fred/series/observations",
                           params={"series_id": series_id, "api_key": api_key,
                                   "file_type": "json", "sort_order": "asc"}, timeout=6)
        obs = res.json().get("observations", [])
        out = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]
        return out if out else None
    except Exception:
        return None


@st.cache_data(ttl=3600 * 6, max_entries=5)
def get_global_m2():
    us = fetch_fred_series("M2SL")
    eu = fetch_fred_series("MYAGM2EZM196N")
    if not us:
        return None
    eur_usd = 1.08
    us_map = {d[:7]: v for d, v in us}
    eu_map = {d[:7]: v for d, v in eu} if eu else {}
    months = sorted(us_map.keys())
    trend = []
    for m in months[-24:]:
        total = us_map[m] / 1000.0
        if m in eu_map:
            total += (eu_map[m] * eur_usd) / 1e12
        trend.append(total)
    if not trend:
        return None
    yoy = None
    if len(months) > 12:
        bm = months[-13]
        base = us_map[bm] / 1000.0 + ((eu_map.get(bm, 0) * eur_usd) / 1e12)
        if base:
            yoy = (trend[-1] - base) / base * 100
    return {"total_trillion": trend[-1], "yoy": yoy, "trend": trend}


@st.cache_data(ttl=3600 * 4, max_entries=5)
def get_high_yield_spread():
    data = fetch_fred_series("BAMLH0A0HYM2")
    if not data:
        return None
    date, value = data[-1]
    return {"value": value}


@st.cache_data(ttl=300, max_entries=5)
def get_cnn_fear_greed():
    try:
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                           headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        d = res.json()
        score = int(round(d["fear_and_greed"]["score"]))
        rating = d["fear_and_greed"]["rating"]
        kr = {"extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립",
              "greed": "탐욕", "extreme greed": "극단적 탐욕"}
        return score, kr.get(rating, rating)
    except Exception:
        return 50, "중립"


def is_korean(ticker):
    return ticker.endswith(".KS") or ticker.endswith(".KQ")


def get_current_price(ticker):
    if is_korean(ticker):
        return get_naver_domestic_price(ticker)
    q = get_finnhub_quote(ticker)
    return q["current"] if q else None


@st.cache_data(ttl=300, max_entries=60)
def get_52w_high(ticker):
    """52주 고점 (해외는 Finnhub 캔들, 국내는 생략)."""
    if is_korean(ticker):
        return None
    api_key = _secret("FINNHUB_API_KEY")
    if not api_key:
        return None
    try:
        import time
        sym = FINNHUB_SYMBOLS.get(ticker, ticker)
        now = int(time.time())
        res = requests.get("https://finnhub.io/api/v1/stock/candle",
                           params={"symbol": sym, "resolution": "D",
                                   "from": now - 252 * 86400, "to": now, "token": api_key},
                           timeout=5)
        d = res.json()
        if d.get("h"):
            return max(d["h"])
    except Exception:
        pass
    return None


@st.cache_data(ttl=300, max_entries=60)
def get_rsi(ticker):
    """RSI 14일 (해외만, Finnhub 캔들 기반)."""
    if is_korean(ticker):
        return None
    api_key = _secret("FINNHUB_API_KEY")
    if not api_key:
        return None
    try:
        import time
        sym = FINNHUB_SYMBOLS.get(ticker, ticker)
        now = int(time.time())
        res = requests.get("https://finnhub.io/api/v1/stock/candle",
                           params={"symbol": sym, "resolution": "D",
                                   "from": now - 40 * 86400, "to": now, "token": api_key},
                           timeout=5)
        d = res.json()
        closes = d.get("c")
        if not closes or len(closes) < 15:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    except Exception:
        return None


# ========== 유틸 ==========
def fmt_won(v):
    return f"{v:,.0f}원"


def fmt_usd(v, decimals=2):
    return f"{v:,.{decimals}f}&#36;"


def make_sparkline(values, width=340, height=110, color="#4dd2ff"):
    if not values or len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = (mx - mn) or 1
    px, py = 3, 6
    step = (width - px * 2) / (len(values) - 1)
    pts = [(px + i * step, py + (height - py * 2) * (1 - (v - mn) / rng)) for i, v in enumerate(values)]
    pstr = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{px},{height-py} " + pstr + f" {width-px},{height-py}"
    lx, ly = pts[-1]
    return (f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" style="display:block;max-width:100%;">'
            f'<polygon points="{area}" fill="{color}" opacity="0.15"/>'
            f'<polyline points="{pstr}" fill="none" stroke="{color}" stroke-width="2.5" '
            f'stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{color}"/></svg>')


# ========== 시장 지표 ==========
def render_market():
    krw = get_usd_krw()
    fg_score, fg_status = get_cnn_fear_greed()
    dxy = get_finnhub_quote("UUP")
    vix = get_finnhub_quote("^VIX")  # 무료 미지원일 수 있음
    shy = get_finnhub_quote("SHY")
    tlt = get_finnhub_quote("TLT")
    m2 = get_global_m2()
    hy = get_high_yield_spread()

    def cell(label, main, sub="", sub_color="#4d94ff"):
        sub_html = f'<div style="font-size:11px;font-weight:700;color:{sub_color};white-space:nowrap;">{sub}</div>' if sub else ""
        return ('<div style="flex:1 1 0;min-width:0;padding:8px 6px;border-right:1px solid #222;text-align:center;">'
                f'<div style="font-size:11px;color:#aaa;white-space:nowrap;">{label}</div>'
                f'<div style="font-size:14px;font-weight:800;color:#fff;margin-top:2px;white-space:nowrap;">{main}</div>'
                f'{sub_html}</div>')

    # 환율
    krw_cell = cell("환율", f"₩{krw:,.1f}")
    # 공포탐욕
    fg_cell = cell("공포·탐욕", f"{fg_score}", fg_status, "#ccc")
    # 달러인덱스
    if dxy:
        c = "#ff4d4d" if dxy["change_pct"] >= 0 else "#4d94ff"
        a = "▲" if dxy["change_pct"] >= 0 else "▼"
        dxy_cell = cell("달러인덱스", f"${dxy['current']:,.2f}", f"{a} {abs(dxy['change_pct']):.2f}%", c)
    else:
        dxy_cell = cell("달러인덱스", "-")
    # 빅스
    if vix:
        v = vix["current"]
        msg, mc = ("안정", "#4dff4d") if v < 15 else ("유의", "#ffff4d") if v < 20 else ("경계", "#ff944d") if v < 30 else ("위험", "#ff4d4d")
        vix_cell = cell("빅스 VIX", f"{v:.2f}", msg, mc)
    else:
        vix_cell = cell("빅스 VIX", "-")
    # 국채 (SHY=단기, TLT=장기 프록시)
    bond_main = "-"
    if shy or tlt:
        parts = []
        if shy:
            parts.append(f"단기 ${shy['current']:.1f}")
        if tlt:
            parts.append(f"장기 ${tlt['current']:.1f}")
        bond_main = "<br>".join(parts)
    bond_cell = ('<div style="flex:1 1 0;min-width:0;padding:8px 6px;text-align:center;">'
                 '<div style="font-size:11px;color:#aaa;white-space:nowrap;">🇺🇸 국채</div>'
                 f'<div style="font-size:12px;font-weight:700;color:#fff;margin-top:2px;white-space:nowrap;">{bond_main}</div></div>')

    st.markdown(
        '<div style="display:flex;background:#111;border-radius:8px;overflow:hidden;margin-bottom:6px;flex-wrap:wrap;">'
        + krw_cell + fg_cell + dxy_cell + vix_cell + bond_cell + '</div>',
        unsafe_allow_html=True)

    # 하이일드 + M2 (한 줄 더, 있을 때만)
    extra = []
    if hy:
        v = hy["value"]
        sig, sc = ("🟢안정", "#4dff4d") if v < 3.5 else ("🟡보통", "#ffff4d") if v < 5 else ("🟠경계", "#ff944d") if v < 8 else ("🔴위험", "#ff4d4d")
        extra.append(cell("하이일드", f"{v:.2f}%", sig, sc))
    if m2:
        yoy = m2.get("yoy")
        yt = ""
        if yoy is not None:
            yc = "#ff4d4d" if yoy >= 0 else "#4d94ff"
            yt = f'<span style="color:{yc};font-size:10px;">{"▲" if yoy>=0 else "▼"}{abs(yoy):.1f}%</span>'
        extra.append('<div style="flex:1 1 0;min-width:0;padding:8px 6px;text-align:center;">'
                     '<div style="font-size:11px;color:#aaa;">글로벌 M2</div>'
                     f'<div style="font-size:13px;font-weight:800;color:#fff;margin-top:2px;">${m2["total_trillion"]:,.1f}T {yt}</div></div>')
    if extra:
        st.markdown('<div style="display:flex;background:#111;border-radius:8px;overflow:hidden;margin-bottom:6px;">'
                    + "".join(extra) + '</div>', unsafe_allow_html=True)

    # M2 추세 차트 (접이식)
    if m2 and m2.get("trend") and len(m2["trend"]) >= 2:
        with st.expander("📈 글로벌 M2 추세 차트 (최근 24개월)"):
            trend = m2["trend"]
            spark = make_sparkline(trend, color="#4dd2ff" if (m2.get("yoy") or 0) >= 0 else "#ff6f4d")
            first, last = trend[0], trend[-1]
            chg = ((last - first) / first * 100) if first else 0
            cc = "#ff4d4d" if chg >= 0 else "#4d94ff"
            st.markdown(
                '<div style="background:#0d0d0d;border-radius:10px;padding:14px 16px;">'
                '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">'
                '<span style="font-size:14px;font-weight:700;color:#fff;">글로벌 M2 통화량</span>'
                f'<span style="font-size:18px;font-weight:800;color:#fff;">${last:,.1f}T</span></div>'
                f'<div style="height:110px;">{spark}</div>'
                '<div style="display:flex;justify-content:space-between;font-size:11px;color:#888;margin-top:6px;">'
                f'<span>24개월 전 ${first:,.1f}T</span>'
                f'<span style="color:{cc};font-weight:700;">24개월 {"▲" if chg>=0 else "▼"} {abs(chg):.1f}%</span></div>'
                '<div style="font-size:10px;color:#666;margin-top:8px;">※ 미국 M2 + 유로존 M2 합산 (FRED)</div></div>',
                unsafe_allow_html=True)


# ========== 포트폴리오 계산 ==========
def compute_account(holdings, cur_fx):
    """계좌의 종목별 계산 + 원화 합계 (해외+국내 모두 정확히 합산)."""
    rows = []
    usd_buy_krw = usd_eval_krw = usd_fx_gain = 0.0
    krw_buy = krw_eval = 0.0
    has_usd = False
    for h in holdings:
        tk = h["ticker"]
        price = get_current_price(tk)
        buy_amt = h["qty"] * h["avg_price"]
        eval_amt = h["qty"] * price if price is not None else buy_amt
        usd = not is_korean(tk)
        buy_fx = h.get("buy_fx_rate", 0) or cur_fx
        if usd:
            has_usd = True
            usd_buy_krw += buy_amt * buy_fx
            usd_eval_krw += eval_amt * cur_fx
            usd_fx_gain += buy_amt * (cur_fx - buy_fx)
        else:
            krw_buy += buy_amt
            krw_eval += eval_amt
        rows.append({**h, "price": price, "buy_amt": buy_amt, "eval_amt": eval_amt,
                     "usd": usd, "buy_fx": buy_fx,
                     "rsi": get_rsi(tk), "high52": get_52w_high(tk)})
    total_buy_krw = usd_buy_krw + krw_buy
    total_eval_krw = usd_eval_krw + krw_eval
    return {
        "rows": rows,
        "total_buy_krw": total_buy_krw,
        "total_eval_krw": total_eval_krw,
        "fx_gain": usd_fx_gain,
        "has_usd": has_usd,
        "usd_buy": sum(r["buy_amt"] for r in rows if r["usd"]),
        "usd_eval": sum(r["eval_amt"] for r in rows if r["usd"]),
    }


# ========== 다이얼로그 ==========
@st.dialog("새 계좌 만들기")
def create_account_dialog():
    name = st.text_input("계좌 이름", placeholder="예: 3. 직투")
    if st.button("만들기", use_container_width=True):
        if name and name not in st.session_state.portfolios:
            st.session_state.portfolios[name] = []
            save_portfolios()
            st.rerun()


@st.dialog("종목 추가")
def add_stock_dialog(acct):
    st.caption("티커 입력 (미국: AAPL, QLD / 국내: 005930.KS)")
    ticker = st.text_input("티커").strip().upper()
    name = st.text_input("종목명 (표시용)")
    qty = st.number_input("수량", min_value=0.0, step=1.0)
    avg = st.number_input("평단가", min_value=0.0, step=0.0001, format="%.4f")
    target = st.number_input("목표 비중 (%)", min_value=0.0, max_value=100.0, step=1.0)
    fx = 0.0
    if ticker and not is_korean(ticker):
        fx = st.number_input("매수 환율 (원/달러)", min_value=0.0, step=1.0,
                             help="매수 시점 환율. 0이면 현재환율 적용")
    if st.button("추가", use_container_width=True):
        if ticker:
            st.session_state.portfolios[acct].append({
                "ticker": ticker, "name": name or ticker, "qty": qty,
                "avg_price": avg, "target_weight": target, "buy_fx_rate": fx})
            save_portfolios()
            st.rerun()


@st.dialog("추가 매수 (누적)")
def add_more_dialog(acct, idx):
    h = st.session_state.portfolios[acct][idx]
    st.markdown(f"**{h['name']}** 기존: {h['qty']}주 @ {h['avg_price']:.4f}")
    add_qty = st.number_input("추가 수량", min_value=0.0, step=1.0)
    add_price = st.number_input("매수 단가", min_value=0.0, step=0.0001, format="%.4f")
    add_fx = 0.0
    if not is_korean(h["ticker"]):
        add_fx = st.number_input("매수 환율", min_value=0.0, step=1.0)
    if add_qty > 0 and add_price > 0:
        new_qty = h["qty"] + add_qty
        new_avg = (h["qty"] * h["avg_price"] + add_qty * add_price) / new_qty
        old_fx = h.get("buy_fx_rate", 0) or 0
        if not is_korean(h["ticker"]) and add_fx > 0:
            new_fx = (h["qty"] * old_fx + add_qty * add_fx) / new_qty if old_fx else add_fx
        else:
            new_fx = old_fx
        st.info(f"→ 누적: {new_qty}주 @ {new_avg:.4f}" + (f" (환율 {new_fx:.0f})" if new_fx else ""))
        if st.button("확정", use_container_width=True):
            h["qty"] = new_qty
            h["avg_price"] = new_avg
            h["buy_fx_rate"] = new_fx
            save_portfolios()
            st.rerun()


@st.dialog("종목 수정 / 삭제")
def edit_stock_dialog(acct, idx):
    h = st.session_state.portfolios[acct][idx]
    name = st.text_input("종목명", value=h["name"])
    qty = st.number_input("수량", min_value=0.0, step=1.0, value=float(h["qty"]))
    avg = st.number_input("평단가", min_value=0.0, step=0.0001, format="%.4f", value=float(h["avg_price"]))
    target = st.number_input("목표 비중 (%)", min_value=0.0, max_value=100.0, step=1.0,
                             value=float(h.get("target_weight", 0)))
    fx = float(h.get("buy_fx_rate", 0) or 0)
    if not is_korean(h["ticker"]):
        fx = st.number_input("매수 환율", min_value=0.0, step=1.0, value=fx)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("저장", use_container_width=True):
            h.update({"name": name, "qty": qty, "avg_price": avg,
                      "target_weight": target, "buy_fx_rate": fx})
            save_portfolios()
            st.rerun()
    with c2:
        if st.button("🗑 삭제", use_container_width=True):
            st.session_state.portfolios[acct].pop(idx)
            save_portfolios()
            st.rerun()


# ========== 종목 카드 렌더 ==========
def render_holdings(acct, data, cur_fx, show_krw):
    rows = data["rows"]
    total_eval = sum(r["eval_amt"] for r in rows) or 1
    for i, r in enumerate(rows):
        usd = r["usd"]
        price = r["price"]
        buy_fx = r["buy_fx"]
        profit = r["eval_amt"] - r["buy_amt"]
        profit_pct = (profit / r["buy_amt"] * 100) if r["buy_amt"] else 0
        pc = "#ff4d4d" if profit >= 0 else "#4d94ff"
        pa = "▲" if profit >= 0 else "▼"
        cur_w = r["eval_amt"] / total_eval * 100
        tgt_w = r.get("target_weight", 0) or 0

        def money(v, is_price=False):
            if usd:
                if show_krw:
                    rate = cur_fx
                    return fmt_won(v * rate)
                return fmt_usd(v, 2 if is_price else 2)
            return fmt_won(v)

        # RSI 신호
        rsi = r.get("rsi")
        if rsi is not None:
            rc = "#4d94ff" if rsi <= 30 else "#ff4d4d" if rsi >= 70 else "#999"
            sig = "매수" if rsi <= 30 else "매도" if rsi >= 70 else "중립"
            rsi_html = f'RSI <b style="color:{rc};">{rsi:.0f}</b> <span style="color:{rc};">{sig}</span>'
        else:
            rsi_html = '<span style="color:#666;">RSI -</span>'
        # 52주 고점대비
        high52 = r.get("high52")
        if high52 and price:
            drop = (price - high52) / high52 * 100
            drop_html = f'<span style="color:#4d94ff;">{drop:.1f}%</span>'
        else:
            drop_html = '<span style="color:#666;">-</span>'
        # 조정 금액
        tgt_amt = tgt_w / 100 * total_eval
        short = tgt_amt - r["eval_amt"]
        if abs(short) < total_eval * 0.005:
            adj = '<span style="color:#888;">적정</span>'
        elif short > 0:
            adj = f'<span style="color:#ff4d4d;">매수 {money(short)}</span>'
        else:
            adj = f'<span style="color:#4d94ff;">매도 {money(abs(short))}</span>'

        st.markdown(
            f'<div style="background:#141414;border:1px solid #262626;border-radius:10px;padding:12px 14px;margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
            f'<span style="font-size:15px;font-weight:800;color:#fff;">{r["name"]} <span style="font-size:11px;color:#888;">({r["ticker"]})</span></span>'
            f'<span style="font-size:13px;color:#aaa;">수량&nbsp;&nbsp;<b style="color:#fff;">{r["qty"]:,.0f}</b></span></div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:13px;">'
            f'<div><span style="color:#888;font-size:11px;">신호</span><br>{rsi_html}</div>'
            f'<div><span style="color:#888;font-size:11px;">52주고점대비</span><br>{drop_html}</div>'
            f'<div><span style="color:#888;font-size:11px;">현재가</span><br><b>{money(price, True) if price else "-"}</b></div>'
            f'<div><span style="color:#888;font-size:11px;">평단가</span><br><b>{money(r["avg_price"], True)}</b></div>'
            f'<div><span style="color:#888;font-size:11px;">매수금</span><br><b>{money(r["buy_amt"])}</b></div>'
            f'<div><span style="color:#888;font-size:11px;">평가금</span><br><b>{money(r["eval_amt"])}</b></div>'
            f'<div><span style="color:#888;font-size:11px;">손익금</span><br><b style="color:{pc};">{pa} {money(abs(profit))}</b></div>'
            f'<div><span style="color:#888;font-size:11px;">손익률</span><br><b style="color:{pc};">{pa} {abs(profit_pct):.1f}%</b></div>'
            f'<div><span style="color:#888;font-size:11px;">비중/목표</span><br><b>{cur_w:.0f}% / {tgt_w:.0f}%</b></div>'
            f'<div><span style="color:#888;font-size:11px;">조정필요</span><br>{adj}</div>'
            f'</div></div>',
            unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("추가매수", key=f"more_{acct}_{i}", use_container_width=True):
                add_more_dialog(acct, i)
        with c2:
            if st.button("수정·삭제", key=f"edit_{acct}_{i}", use_container_width=True):
                edit_stock_dialog(acct, i)


def build_donut(items, size=160):
    """items: [(name, value)] → 도넛 SVG + 범례."""
    tot = sum(v for _, v in items) or 1
    palette = ["#4dd2ff", "#ff9f4d", "#4dff88", "#ff4d4d", "#c04dff", "#ffd633",
               "#4d94ff", "#ff4dcb", "#9fe14d", "#4dffea", "#ff6f4d", "#8888ff"]
    ro, ri = size / 2 - 4, size / 2 - 32
    cx = cy = size / 2
    segs = labs = leg = ""
    ang = -90.0
    for i, (nm, av) in enumerate(sorted(items, key=lambda x: -x[1])):
        col = palette[i % len(palette)]
        w = av / tot * 100
        sw = w / 100 * 360
        a0, a1 = math.radians(ang), math.radians(ang + sw)
        x0o, y0o = cx + ro * math.cos(a0), cy + ro * math.sin(a0)
        x1o, y1o = cx + ro * math.cos(a1), cy + ro * math.sin(a1)
        x0i, y0i = cx + ri * math.cos(a1), cy + ri * math.sin(a1)
        x1i, y1i = cx + ri * math.cos(a0), cy + ri * math.sin(a0)
        lg = 1 if sw > 180 else 0
        segs += (f'<path d="M {x0o:.1f} {y0o:.1f} A {ro} {ro} 0 {lg} 1 {x1o:.1f} {y1o:.1f} '
                 f'L {x0i:.1f} {y0i:.1f} A {ri} {ri} 0 {lg} 0 {x1i:.1f} {y1i:.1f} Z" fill="{col}"/>')
        if w >= 7:
            ma = math.radians(ang + sw / 2)
            rm = (ro + ri) / 2
            segs += f'<text x="{cx+rm*math.cos(ma):.1f}" y="{cy+rm*math.sin(ma)+3:.1f}" text-anchor="middle" font-size="11" font-weight="800" fill="#0a0a0a">{w:.0f}%</text>'
        leg += ('<div style="display:flex;align-items:center;gap:6px;margin:3px 0;">'
                f'<span style="width:10px;height:10px;border-radius:2px;background:{col};flex:0 0 auto;"></span>'
                f'<span style="font-size:12px;color:#ddd;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{nm}</span>'
                f'<span style="font-size:12px;color:#fff;font-weight:700;">{w:.1f}%</span></div>')
        ang += sw
    donut = f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">{segs}</svg>'
    return (f'<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;">'
            f'<div style="flex:0 0 auto;">{donut}</div>'
            f'<div style="flex:1 1 180px;min-width:180px;">{leg}</div></div>')


# ==========================================================
# 메인 페이지
# ==========================================================
if "portfolios" not in st.session_state:
    st.session_state.portfolios = load_portfolios()

st.markdown("<h4 style='margin:0 0 10px;'>📈 시장 지표</h4>", unsafe_allow_html=True)
render_market()

st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)
tc = st.columns([3, 1])
with tc[0]:
    st.markdown("<h3 style='margin:0;padding:6px 0;font-weight:800;'>"
                "<span style='display:inline-block;width:5px;height:22px;background:linear-gradient(180deg,#4dd2ff,#4d94ff);border-radius:2px;margin-right:10px;vertical-align:-3px;'></span>"
                "내 포트폴리오</h3>", unsafe_allow_html=True)
with tc[1]:
    if st.button("＋ 계좌", use_container_width=True):
        create_account_dialog()

if not st.session_state.portfolios:
    st.info("아직 계좌가 없습니다. 오른쪽 위 '＋ 계좌'로 만들어보세요.")
else:
    cur_fx = get_usd_krw()
    names = list(st.session_state.portfolios.keys())

    # 통화 토글
    cmode = st.radio("통화", ["$ 달러", "₩ 원화"], horizontal=True,
                     key="currency_mode", label_visibility="collapsed")
    show_krw_global = (cmode == "₩ 원화")

    # 1단계: 전 계좌 계산
    acct_data = {}
    grand_buy = grand_eval = grand_fx = 0.0
    grand_holdings = []
    for nm in names:
        d = compute_account(st.session_state.portfolios[nm], cur_fx)
        acct_data[nm] = d
        sel = st.session_state.get(f"sel_{nm}", True)
        if sel and d["total_eval_krw"] > 0:
            grand_buy += d["total_buy_krw"]
            grand_eval += d["total_eval_krw"]
            grand_fx += d["fx_gain"]
            for r in d["rows"]:
                ev = r["eval_amt"] * cur_fx if r["usd"] else r["eval_amt"]
                grand_holdings.append((r["name"], ev))

    # 2단계: 총합산 (4개 숫자 항상 + 도넛 접기)
    if grand_eval > 0:
        gp = grand_eval - grand_buy
        gpp = (gp / grand_buy * 100) if grand_buy else 0
        gc = "#ff4d4d" if gp >= 0 else "#4d94ff"
        ga = "▲" if gp >= 0 else "▼"
        st.markdown(
            '<div style="background:linear-gradient(135deg,#151d2a,#0f1620);border:1px solid #2a3a52;border-radius:12px;padding:16px 18px;margin:8px 0 10px;">'
            '<div style="font-size:14px;font-weight:800;color:#4dd2ff;margin-bottom:10px;">선택 계좌 총 합산 (원화)</div>'
            '<div style="display:flex;gap:14px 24px;flex-wrap:wrap;">'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 매수금액</div><div style="font-size:17px;font-weight:800;color:#fff;">{grand_buy:,.0f}원</div></div>'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 평가금액</div><div style="font-size:19px;font-weight:800;color:#fff;">{grand_eval:,.0f}원</div></div>'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 손익</div><div style="font-size:17px;font-weight:800;color:{gc};">{ga} {abs(gp):,.0f}원</div></div>'
            f'<div style="flex:1 1 120px;"><div style="font-size:11px;color:#888;">총 손익률</div><div style="font-size:17px;font-weight:800;color:{gc};">{ga} {abs(gpp):.1f}%</div></div>'
            '</div></div>', unsafe_allow_html=True)
        # 종목별 도넛 (접기)
        merged = {}
        for nm, ev in grand_holdings:
            merged[nm] = merged.get(nm, 0) + ev
        if merged:
            with st.expander("종목별 비중 보기 (전체 합산)"):
                st.markdown(build_donut(list(merged.items())), unsafe_allow_html=True)
    else:
        st.info("아래에서 계좌를 선택하면 총 합산이 표시됩니다.")

    st.markdown("<div style='font-size:13px;color:#888;margin:12px 0 6px;'>계좌 목록</div>", unsafe_allow_html=True)

    # 3단계: 계좌별 요약 + 상세 접기
    for nm in names:
        d = acct_data[nm]
        holdings = st.session_state.portfolios[nm]
        buy_krw, eval_krw = d["total_buy_krw"], d["total_eval_krw"]
        profit = eval_krw - buy_krw
        ppct = (profit / buy_krw * 100) if buy_krw else 0
        pc = "#ff4d4d" if profit >= 0 else "#4d94ff"
        pa = "▲" if profit >= 0 else "▼"
        summary = (f'<span style="color:#fff;font-weight:700;">{eval_krw:,.0f}원</span> '
                   f'<span style="color:{pc};font-weight:700;">{pa} {abs(ppct):.1f}%</span>') if buy_krw else '<span style="color:#666;">종목 없음</span>'

        st.markdown(
            f'<div style="background:#141414;border:1px solid #262626;border-radius:10px;padding:12px 14px 4px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div><span style="font-size:16px;font-weight:800;color:#fff;">{nm}</span> '
            f'<span style="font-size:12px;color:#888;">({len(holdings)}종목)</span></div>'
            f'<div>{summary}</div></div></div>', unsafe_allow_html=True)

        cc = st.columns(2)
        with cc[0]:
            st.checkbox("✓ 합산 포함", value=True, key=f"sel_{nm}")
        with cc[1]:
            expanded = st.checkbox("📂 상세 보기", value=False, key=f"exp_{nm}")

        if expanded:
            show_krw = show_krw_global
            # 계좌 상세 요약
            if buy_krw > 0:
                fx = d["fx_gain"]
                fxc = "#ff4d4d" if fx >= 0 else "#4d94ff"
                fxa = "▲" if fx >= 0 else "▼"
                fx_line = (f'<div style="font-size:12px;color:#888;margin-top:6px;">환차손익 '
                           f'<b style="color:{fxc};">{fxa} {abs(fx):,.0f}원</b></div>') if d["has_usd"] else ""
                st.markdown(
                    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;margin:8px 0;">'
                    f'<div><span style="font-size:11px;color:#888;">총매수</span><br><b style="color:#fff;">{buy_krw:,.0f}원</b></div>'
                    f'<div><span style="font-size:11px;color:#888;">총평가</span><br><b style="color:#fff;">{eval_krw:,.0f}원</b></div>'
                    f'<div><span style="font-size:11px;color:#888;">평가손익</span><br><b style="color:{pc};">{pa} {abs(profit):,.0f}원</b></div>'
                    f'<div><span style="font-size:11px;color:#888;">손익률</span><br><b style="color:{pc};">{pa} {abs(ppct):.1f}%</b></div>'
                    f'</div>{fx_line}', unsafe_allow_html=True)

            bc = st.columns(2)
            with bc[0]:
                if st.button("＋ 종목 추가", key=f"add_{nm}", use_container_width=True):
                    add_stock_dialog(nm)
            with bc[1]:
                if st.button("🗑 계좌 삭제", key=f"del_{nm}", use_container_width=True):
                    del st.session_state.portfolios[nm]
                    save_portfolios()
                    st.rerun()

            if holdings:
                st.markdown("<hr style='border-color:#222;margin:8px 0;'>", unsafe_allow_html=True)
                render_holdings(nm, d, cur_fx, show_krw)
                # 계좌 종목별 도넛 (접기)
                items = [(r["name"], r["eval_amt"] * (cur_fx if r["usd"] else 1)) for r in d["rows"] if r["eval_amt"]]
                if items:
                    with st.expander("📊 이 계좌 종목별 비중"):
                        st.markdown(build_donut(items, size=150), unsafe_allow_html=True)

        st.markdown("<div style='height:10px;border-bottom:1px solid #1a1a1a;margin-bottom:10px;'></div>", unsafe_allow_html=True)
