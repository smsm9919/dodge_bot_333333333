# -*- coding: utf-8 -*-
import time, requests, hmac, hashlib, json, os
import pandas as pd, numpy as np
from flask import Flask, render_template_string
from threading import Thread
from collections import deque
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

try:
    from termcolor import colored
except Exception:
    def colored(x, *args, **kwargs): return x

app = Flask(__name__)

# ===== Metrics & State =====
total_trades = 0
successful_trades = 0
failed_trades = 0
trade_log = deque(maxlen=50)
compound_profit = 0.0
last_direction = None

COOLDOWN_PERIOD = 600
last_trade_time = 0

PARAMS = {
    "min_range_pct": 1.5,
    "spike_atr_mult": 1.8,
    "noise_pct": 0.25,
    "adx_min": 25,
    "rsi_lb": 45,
    "rsi_ub": 55,
    "min_atr_pct": 0.5,
    "max_atr_pct": 3.0,
    "vol_boost": 1.2
}

API_KEY = os.getenv("BINGX_API_KEY", "")
API_SECRET = os.getenv("BINGX_API_SECRET", "")
BASE_URL = "https://open-api.bingx.com"

SYMBOL = os.getenv("SYMBOL", "DOGE-USDT")
INTERVAL = os.getenv("INTERVAL", "15m")
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
TRADE_PORTION = float(os.getenv("TRADE_PORTION", "0.60"))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
TOLERANCE = float(os.getenv("TOLERANCE", "0.0005"))
MIN_ATR = float(os.getenv("MIN_ATR", "0.001"))
MIN_TP_PERCENT = float(os.getenv("MIN_TP_PERCENT", "0.75"))

position_open = False
position_side = None
entry_price = 0.0
tp_price = 0.0
sl_price = 0.0
current_quantity = 0.0
current_atr = 0.0
current_pnl = 0.0
current_price = 0.0
ema_200_value = 0.0
rsi_value = 0.0
adx_value = 0.0
update_time = ""
initial_balance = 0.0

# ===== Dashboard =====
@app.route('/')
def dashboard():
    return render_template_string('''
    <html><head><meta charset="utf-8"><title>DOGE Bot</title>
    <style>
    body{font-family:system-ui;background:#0b1220;color:#e8edf7;margin:0}
    .w{max-width:1100px;margin:auto;padding:22px}
    .g{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
    .c{background:#121b2e;border-radius:12px;padding:16px}
    .k{opacity:.7}.v{font-weight:700}.row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1f2a44}
    .row:last-child{border-bottom:0}.pos{color:#10b981}.neg{color:#ef4444}.log{max-height:420px;overflow:auto;font-family:ui-monospace}
    .item{padding:10px;border-left:4px solid #334;margin:8px 0;background:#0d1526;border-radius:8px}
    .tp{border-left-color:#10b981}.sl{border-left-color:#ef4444}
    </style></head><body><div class="w">
    <h2>🪙 DOGE/USDT Bot</h2>
    <div class="g">
      <div class="c">
        <div class="row"><div class="k">Total Trades</div><div class="v">{{total_trades}}</div></div>
        <div class="row"><div class="k">Wins</div><div class="v pos">{{successful_trades}}</div></div>
        <div class="row"><div class="k">Losses</div><div class="v neg">{{failed_trades}}</div></div>
        <div class="row"><div class="k">Compound P&L</div><div class="v {% if compound_profit>=0 %}pos{% else %}neg{% endif %}">{{compound_profit|round(4)}} USDT</div></div>
      </div>
      <div class="c">
        <div class="row"><div class="k">Leverage</div><div class="v">{{lev}}x</div></div>
        <div class="row"><div class="k">Risk/Trade</div><div class="v">{{risk*100|round(0)}}%</div></div>
        <div class="row"><div class="k">TP / SL</div><div class="v">1.2×ATR / 0.8×ATR</div></div>
        <div class="row"><div class="k">Cooldown</div><div class="v">10m</div></div>
      </div>
      <div class="c">
        {% if price and ema200 %}
          <div class="row"><div class="k">Price</div><div class="v">{{price|round(5)}}</div></div>
          <div class="row"><div class="k">EMA200</div><div class="v">{{ema200|round(5)}}</div></div>
          <div class="row"><div class="k">RSI/ADX</div><div class="v">{{rsi|round(1)}} / {{adx|round(1)}}</div></div>
          <div class="row"><div class="k">Regime</div><div class="v">{{"Bull" if price>ema200 else "Bear"}}</div></div>
        {% else %}Loading...{% endif %}
      </div>
      <div class="c">
        {% if pos_open %}
          <div class="row"><div class="k">Status</div><div class="v pos">ACTIVE</div></div>
          <div class="row"><div class="k">Side</div><div class="v">{{pos_side}}</div></div>
          <div class="row"><div class="k">Entry</div><div class="v">{{pos_entry|round(5)}}</div></div>
          <div class="row"><div class="k">TP/SL</div><div class="v">{{pos_tp|round(5)}} / {{pos_sl|round(5)}}</div></div>
          <div class="row"><div class="k">PNL</div><div class="v">{{pnl|round(4)}} USDT</div></div>
        {% else %}🔴 No open position{% endif %}
        <div class="k" style="margin-top:8px">Last: {{ut}}</div>
      </div>
    </div>
    <div class="c" style="margin-top:16px">
      <div class="k">Recent</div><div class="log">
        {% if log %}{% for t in log %}
        <div class="item {{'tp' if t.result=='TP' else 'sl'}}">{{t.time}} — {{t.side}} {{t.entry_price|round(5)}} → {{t.exit_price|round(5)}} — <b class="{{'pos' if t.profit>=0 else 'neg'}}">{{t.profit|round(4)}} USDT</b></div>
        {% endfor %}{% else %}<div class="item">No trades yet</div>{% endif %}
    </div></div></div></body></html>
    ''',
    total_trades=total_trades, successful_trades=successful_trades, failed_trades=failed_trades,
    compound_profit=compound_profit, lev=LEVERAGE, risk=TRADE_PORTION,
    log=trade_log, pos_open=position_open, pos_side=position_side,
    pos_entry=entry_price, pos_tp=tp_price, pos_sl=sl_price, pnl=current_pnl,
    price=current_price, ema200=ema_200_value, rsi=rsi_value, adx=adx_value, ut=update_time)

@app.route('/healthz')
def healthz(): return "ok", 200

def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)

# ===== API =====
def get_signature(params):
    qs = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def safe_api_request(method, endpoint, params=None, data=None):
    try:
        url = f"{BASE_URL}{endpoint}"; headers = {"X-BX-APIKEY": API_KEY}
        ts = str(int(time.time()*1000)); params = params or {}; params["timestamp"]=ts; params["signature"]=get_signature(params)
        r = requests.request(method, url, headers=headers, params=params, json=data, timeout=10)
        return r.json() if r.status_code==200 else None
    except Exception as e:
        print("API error:", e); return None

def get_balance():
    try:
        ts = str(int(time.time()*1000))
        sig = get_signature({"timestamp": ts})
        url = f"{BASE_URL}/openApi/swap/v2/user/balance?timestamp={ts}&signature={sig}"
        r = requests.get(url, headers={"X-BX-APIKEY": API_KEY}, timeout=10).json()
        if r.get("code")==0:
            data = r.get("data", {})
            if isinstance(data.get("balance"), list):
                for a in data["balance"]:
                    if a.get("asset")=="USDT": return float(a.get("availableBalance",0.0))
            elif isinstance(data.get("balance"), dict):
                a = data["balance"]; 
                if a.get("asset")=="USDT": return float(a.get("availableMargin",0.0))
    except Exception as e: print("balance err:", e)
    return 0.0

def get_open_position():
    try:
        r = safe_api_request("GET","/openApi/swap/v2/user/positions",{"symbol":SYMBOL})
        if r and "data" in r:
            for p in r["data"]:
                if float(p.get("positionAmt",0))!=0:
                    return {"side":"BUY" if float(p["positionAmt"])>0 else "SELL",
                            "entryPrice": float(p["entryPrice"]),
                            "positionAmt": abs(float(p["positionAmt"]))}
    except Exception as e: print("get_open_position err:", e)
    return None

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/klines",
            params={"symbol":SYMBOL,"interval":INTERVAL,"limit":220}, timeout=10)
        data = r.json().get("data", [])
        df = pd.DataFrame(data, columns=["t","o","h","l","c","v"]).astype(float)
        df.columns = ["ts","open","high","low","close","volume"]
        return df
    except Exception as e: print("klines err:", e); return pd.DataFrame()

def calculate_adx(df, period=14):
    try:
        if len(df)<period*2: return pd.Series()
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = high.diff(); minus_dm = -low.diff()
        plus_dm[plus_dm<0]=0; minus_dm[minus_dm<0]=0
        tr = pd.concat([high-low,(high-close.shift(1)).abs(),(low-close.shift(1)).abs()],axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100*(plus_dm.ewm(alpha=1/period).mean()/atr)
        minus_di = 100*(minus_dm.ewm(alpha=1/period).mean()/atr)
        dx = (abs(plus_di-minus_di)/(plus_di+minus_di))*100
        return dx.ewm(alpha=1/period).mean()
    except Exception as e: print("adx err:", e); return pd.Series()

def calculate_ema(series, period):
    if len(series)<period: return pd.Series()
    return series.ewm(span=period, adjust=False).mean()

def price_range_percent(df, lookback=20):
    if len(df)<lookback: return 0.0
    r = df["close"].iloc[-lookback:]; highest, lowest = r.max(), r.min()
    return ((highest-lowest)/max(lowest,1e-9))*100

def calculate_supertrend(df, period=10, multiplier=3):
    try:
        if len(df)<period*2: return pd.Series(), pd.Series()
        high, low, close = df["high"], df["low"], df["close"]
        hl2 = (high+low)/2
        atr = AverageTrueRange(high=high, low=low, close=close, window=period).average_true_range()
        if atr.empty: return pd.Series(), pd.Series()
        upper = hl2 + multiplier*atr; lower = hl2 - multiplier*atr
        direction = pd.Series(np.ones(len(close)), index=df.index)
        for i in range(1,len(close)):
            if close.iloc[i] > upper.iloc[i-1]: direction.iloc[i]=1
            elif close.iloc[i] < lower.iloc[i-1]: direction.iloc[i]=-1
            else:
                direction.iloc[i] = direction.iloc[i-1]
                if direction.iloc[i]==1 and lower.iloc[i] < lower.iloc[i-1]: lower.iloc[i] = lower.iloc[i-1]
                if direction.iloc[i]==-1 and upper.iloc[i] > upper.iloc[i-1]: upper.iloc[i] = upper.iloc[i-1]
        st = np.where(direction==1, lower, upper)
        return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)
    except Exception as e: print("supertrend err:", e); return pd.Series(), pd.Series()

def calculate_tp_sl(entry_price, atr_value, direction):
    if direction=="BUY":
        tp = entry_price + atr_value*1.2; sl = entry_price - atr_value*0.8
    else:
        tp = entry_price - atr_value*1.2; sl = entry_price + atr_value*0.8
    return round(tp,5), round(sl,5)

def create_tp_sl_orders():
    global position_open
    if not position_open or current_quantity<=0 or entry_price<=0: 
        print("TP/SL skip: missing"); return False
    time.sleep(1)
    tp_side = "SELL" if position_side=="BUY" else "BUY"
    sl_side = tp_side
    params_tp = {"symbol":SYMBOL,"side":tp_side,"positionSide":"BOTH","type":"TAKE_PROFIT_MARKET",
                 "quantity":current_quantity,"stopPrice":f"{tp_price:.5f}","workingType":"MARK_PRICE"}
    params_sl = {"symbol":SYMBOL,"side":sl_side,"positionSide":"BOTH","type":"STOP_MARKET",
                 "quantity":current_quantity,"stopPrice":f"{sl_price:.5f}","workingType":"MARK_PRICE"}
    r1 = safe_api_request("POST","/openApi/swap/v2/trade/order",params=params_tp)
    if not (r1 and r1.get("code")==0): print("TP fail", r1); close_position("NO_TP", current_price); return False
    print(f"✅ TP @ {tp_price:.5f}")
    r2 = safe_api_request("POST","/openApi/swap/v2/trade/order",params=params_sl)
    if not (r2 and r2.get("code")==0): print("SL fail", r2); close_position("NO_SL", current_price); return False
    print(f"✅ SL @ {sl_price:.5f}")
    return True

def place_order(side, quantity):
    global position_open, position_side, entry_price, current_quantity, tp_price, sl_price, last_trade_time
    now = time.time()
    if now - last_trade_time < COOLDOWN_PERIOD: print("Cooldown"); return False
    if position_open: print("Position exists"); return False
    atr = max(current_atr, MIN_ATR)
    if current_price<=0: print("Bad price"); return False
    est_tp,_ = calculate_tp_sl(current_price, atr, side)
    tp_percent = abs(est_tp - current_price)/current_price*100
    if tp_percent < MIN_TP_PERCENT: print("TP too small"); return False
    if adx_value < 20: print("ADX weak"); return False
    r = safe_api_request("POST","/openApi/swap/v2/trade/order",
        params={"symbol":SYMBOL,"side":side,"positionSide":"BOTH","type":"MARKET","quantity":quantity})
    if r and r.get("code")==0:
        od = r["data"]; entry = float(od.get("avgPrice") or current_price)
        position_side = side; entry_price = entry; current_quantity = quantity; position_open=True
        tp, sl = calculate_tp_sl(entry_price, atr, position_side)
        globals()["tp_price"], globals()["sl_price"] = tp, sl
        last_trade_time = now
        print(f"{'BUY' if side=='BUY' else 'SELL'} @ {entry_price:.5f} | TP {tp:.5f} | SL {sl:.5f}")
        if not create_tp_sl_orders(): position_open=False; return False
        return True
    print("Place order fail", r); return False

def close_position(reason, exit_price):
    global position_open, position_side, entry_price, current_quantity, tp_price, sl_price
    global total_trades, successful_trades, failed_trades, compound_profit, last_trade_time, last_direction
    if not position_open or position_side is None: return False
    close_side = "SELL" if position_side=="BUY" else "BUY"
    r = safe_api_request("POST","/openApi/swap/v2/trade/order",
        params={"symbol":SYMBOL,"side":close_side,"positionSide":"BOTH","type":"MARKET","quantity":current_quantity})
    if r and r.get("code")==0:
        od = r["data"]; exit_price = float(od.get("avgPrice") or current_price)
        profit = (exit_price-entry_price)*current_quantity if position_side=="BUY" else (entry_price-exit_price)*current_quantity
        compound_profit += profit; total_trades += 1
        if reason=="TP": successful_trades+=1
        else: failed_trades+=1
        trade_log.appendleft({'side':position_side,'entry_price':entry_price,'exit_price':exit_price,'result':reason,'profit':profit,'time':time.strftime("%Y-%m-%d %H:%M:%S")})
        last_direction = position_side; last_trade_time = time.time()
        position_open=False; position_side=None; entry_price=0.0; current_quantity=0.0; tp_price=0.0; sl_price=0.0
        time.sleep(10); return True
    print("Close fail", r); return False

def check_position_status():
    global current_pnl
    if not position_open or position_side is None: return
    current_pnl = (current_price - entry_price) * current_quantity if position_side=="BUY" else (entry_price - current_price) * current_quantity
    if position_side=="BUY":
        if current_price >= tp_price - TOLERANCE: close_position("TP", current_price)
        elif current_price <= sl_price + TOLERANCE: close_position("SL", current_price)
    else:
        if current_price <= tp_price + TOLERANCE: close_position("TP", current_price)
        elif current_price >= sl_price - TOLERANCE: close_position("SL", current_price)

def compute_indicators(df: pd.DataFrame):
    d = df.copy()
    d['ema20'] = calculate_ema(d['close'], 20)
    d['ema50'] = calculate_ema(d['close'], 50)
    d['ema200'] = calculate_ema(d['close'], 200)
    d['rsi'] = RSIIndicator(close=d["close"], window=14).rsi()
    d['adx'] = calculate_adx(d)
    d['atr'] = AverageTrueRange(d['high'], d['low'], d['close'], window=ATR_PERIOD).average_true_range()
    st_line, st_dir = calculate_supertrend(d)
    d['st_dir'] = st_dir
    d['atr_pct'] = (d['atr'] / d['close']) * 100.0
    d['vol_ma20'] = d['volume'].rolling(20).mean()
    return d

def allowed_by_regime(row, side):
    if row['ema200']==0 or np.isnan(row['ema200']): return False
    near = abs(row['close']-row['ema200'])/row['ema200']*100.0
    if near < PARAMS["noise_pct"]: return False
    return (side=="long" and row['close']>row['ema200']) or (side=="short" and row['close']<row['ema200'])

def score_signal(row, prev_row, side):
    s=0
    if side=="long" and row['ema20']>row['ema50'] and prev_row['ema20']<=prev_row['ema50']: s+=1
    if side=="short" and row['ema20']<row['ema50'] and prev_row['ema20']>=prev_row['ema50']: s+=1
    if row['adx']>=PARAMS["adx_min"]: s+=1
    if side=="long" and row['rsi']>max(PARAMS['rsi_ub'],55): s+=1
    if side=="short" and row['rsi']<min(PARAMS['rsi_lb'],45): s+=1
    if side=="long" and row['st_dir']==1: s+=1
    if side=="short" and row['st_dir']==-1: s+=1
    if PARAMS["min_atr_pct"]<=row['atr_pct']<=PARAMS["max_atr_pct"]: s+=1
    if row['vol_ma20']>0 and row['volume']>=row['vol_ma20']*PARAMS['vol_boost']: s+=1
    return s

def main_loop():
    global current_atr, current_price, ema_200_value, rsi_value, adx_value, update_time, initial_balance, last_direction, position_open
    print(colored("🚀 Render bot starting...", "green"))
    initial_balance = get_balance()
    if initial_balance <= 0: print("No balance"); os._exit(1)

    while True:
        try:
            update_time = time.strftime("%Y-%m-%d %H:%M:%S")
            df = get_klines()
            if df.empty: time.sleep(60); continue
            if len(df) < 200: time.sleep(60); continue

            ind = compute_indicators(df)
            current_price = ind["close"].iloc[-1]
            current_atr = ind["atr"].iloc[-1] if not pd.isna(ind["atr"].iloc[-1]) else MIN_ATR
            ema_200_value = ind["ema200"].iloc[-1] if not pd.isna(ind["ema200"].iloc[-1]) else 0
            rsi_value = ind["rsi"].iloc[-2] if len(ind["rsi"])>=2 else 0
            adx_value = ind["adx"].iloc[-1] if len(ind["adx"])>0 else 0
            price_range = price_range_percent(ind)

            current_close = ind["close"].iloc[-1]; previous_close = ind["close"].iloc[-2]
            spike = abs(current_close - previous_close) > current_atr * PARAMS["spike_atr_mult"]

            check_position_status()
            if position_open: time.sleep(15); continue

            now = time.time()
            if now - last_trade_time < COOLDOWN_PERIOD: time.sleep(60); continue
            if spike or price_range <= PARAMS["min_range_pct"]: time.sleep(60); continue

            row, prev = ind.iloc[-1], ind.iloc[-2]
            candidates = []
            for sd in ("long","short"):
                if not allowed_by_regime(row, sd): continue
                sc = score_signal(row, prev, sd)
                if sc >= 4: candidates.append((sd, sc))
            if not candidates: time.sleep(60); continue
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_sd, best_sc = candidates[0]
            desired = "BUY" if best_sd=="long" else "SELL"
            if last_direction == desired: time.sleep(60); continue

            current_balance = get_balance()
            total_balance = initial_balance + compound_profit
            trade_usdt = min(total_balance * TRADE_PORTION, current_balance)
            effective_usdt = trade_usdt * LEVERAGE
            qty = round(max(effective_usdt / max(current_price,1e-9), 0), 2)

            print(f"Signal {desired} score={best_sc} qty={qty}")
            place_order(desired, qty)
            time.sleep(60)
        except Exception as e:
            print("loop err:", e); time.sleep(60)

@app.route('/start')
def start():
    Thread(target=main_loop, daemon=True).start()
    return "started", 200

def run():
    Thread(target=main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")), debug=False)

if __name__ == "__main__":
    run()
