#!/usr/bin/env python3
import os, json, time, threading, webbrowser, http.server, socketserver
import urllib.error, urllib.parse, math, re
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.parse import urlencode

PORT = 8765
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(BASE_DIR, 'dashboard.html')
COINGECKO_BASE  = 'https://api.coingecko.com/api/v3'
DERIBIT_BASE    = 'https://www.deribit.com/api/v2'
FALLBACK_INR_RATE = 83.5
LLM_API_KEY  = os.environ.get('LLM_API_KEY') or ''
LLM_MODEL    = os.environ.get('LLM_MODEL', 'grok-1.5')
LLM_ENDPOINT = os.environ.get('LLM_ENDPOINT', 'https://api.x.ai/v1/chat/completions')
LLM_ENABLED  = os.environ.get('LLM_ENABLED', '1') in ('1', 'true', 'True')
CACHE = {}
CACHE_LOCK = threading.Lock()
TOP_COINS     = []
TOP_COINS_MAP = {}

def cache_get(key, ttl):
    with CACHE_LOCK:
        item = CACHE.get(key)
        if not item: return None
        ts, value = item
        return value if time.time() - ts <= ttl else None

def cache_set(key, value):
    with CACHE_LOCK:
        CACHE[key] = (time.time(), value)

def http_json(url, cache_key=None, cache_ttl=0, timeout=12):
    if cache_key and cache_ttl > 0:
        cached = cache_get(cache_key, cache_ttl)
        if cached is not None: return cached
    req = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'CryptoLens/2.0'})
    with urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8'))
        if cache_key and cache_ttl > 0: cache_set(cache_key, data)
        return data

def http_text(url, cache_key=None, cache_ttl=0, timeout=12):
    if cache_key and cache_ttl > 0:
        cached = cache_get(cache_key, cache_ttl)
        if cached is not None: return cached
    req = Request(url, headers={'Accept': 'text/html,*/*', 'User-Agent': 'CryptoLens/2.0'})
    with urlopen(req, timeout=timeout) as r:
        data = r.read().decode('utf-8', errors='ignore')
        if cache_key and cache_ttl > 0: cache_set(cache_key, data)
        return data

def coingecko_get(path, params=None, cache_ttl=0):
    params = params or {}
    query = urlencode(params)
    url = COINGECKO_BASE + path + ('?' + query if query else '')
    return http_json(url, cache_key='cg:' + path + '?' + query, cache_ttl=cache_ttl)

def deribit_get(method, params=None, cache_ttl=0):
    params = params or {}
    query = urlencode(params)
    url = DERIBIT_BASE + f'/public/{method}' + ('?' + query if query else '')
    return http_json(url, cache_key='dr:' + method + '?' + query, cache_ttl=cache_ttl)

def get_inr_rate():
    cached = cache_get('inr_rate', 300)
    if cached is not None: return cached
    for url in ['https://open.er-api.com/v6/latest/USD','https://api.frankfurter.app/latest?from=USD&to=INR']:
        try:
            data = http_json(url, cache_key='fx:'+url, cache_ttl=300, timeout=8)
            rate = data.get('rates', {}).get('INR')
            if rate is not None:
                rate = float(rate); cache_set('inr_rate', rate); return rate
        except Exception: continue
    return FALLBACK_INR_RATE

def normalize_symbol(symbol):
    return (symbol or '').upper().strip()

def infer_deribit_currency(symbol):
    s = normalize_symbol(symbol)
    return s if s in ('BTC','ETH','SOL','XRP') else None

def load_top_coins(force=False):
    global TOP_COINS, TOP_COINS_MAP
    cached = cache_get('top_coins_master', 300)
    if cached is not None and not force:
        TOP_COINS = cached; TOP_COINS_MAP = {c['id']: c for c in TOP_COINS}; return TOP_COINS
    coins = []
    for page in range(1, 4):
        for order in ('market_cap_desc', 'volume_desc'):
            try:
                batch = coingecko_get('/coins/markets', {'vs_currency':'usd','order':order,'per_page':100,'page':page,'sparkline':'false','price_change_percentage':'24h'}, cache_ttl=120)
                for item in batch or []:
                    item['deribit'] = infer_deribit_currency(item.get('symbol')); coins.append(item)
            except Exception: continue
        if len(coins) >= 250: break
    if not coins:
        coins = [{'id':'bitcoin','symbol':'BTC','name':'Bitcoin','current_price':0,'market_cap':0,'total_volume':0,'circulating_supply':0,'total_supply':0,'max_supply':0,'price_change_percentage_24h':0,'deribit':'BTC'}]
    TOP_COINS = coins[:300]; TOP_COINS_MAP = {c['id']: c for c in TOP_COINS}
    cache_set('top_coins_master', TOP_COINS); return TOP_COINS

def get_coin_history(coin_id):
    for path, params in [('/coins/%s/market_chart'%coin_id, {'vs_currency':'usd','days':'365','interval':'daily'})]:
        try:
            data = coingecko_get(path, params, cache_ttl=300)
            if isinstance(data, dict) and data.get('prices'): return data
        except Exception: continue
    return {'prices': []}

def get_global_sentiment():
    try:
        alt = http_json('https://api.alternative.me/fng/?limit=1', cache_key='fng:latest', cache_ttl=60, timeout=8)
        row = (alt.get('data') or [{}])[0]
        score = int(row.get('value', 50)); label = row.get('value_classification', 'Neutral')
        return {'score': score, 'label': label, 'source': 'Alternative.me Fear & Greed Index', 'drivers': [{'name':'Live Index','value':score},{'name':'Classification','value':label},{'name':'Updated','value':row.get('timestamp','-')}]}
    except Exception:
        return {'score': 50, 'label': 'Neutral', 'source': 'Fallback sentiment', 'drivers': [{'name':'Fallback','value':50}]}

def get_overall_crypto_sentiment():
    return get_global_sentiment()

def get_etf_netflow(selected_symbol=None):
    selected_symbol = normalize_symbol(selected_symbol)
    sources = [('Farside BTC ETF flows','https://farside.co.uk/?p=997','BTC'),('Farside ETH ETF flows','https://farside.co.uk/?p=1321','ETH')]
    if selected_symbol not in ('BTC','ETH'):
        return {'status':'Unavailable','netflow_usd':None,'direction':'Unknown','source':'Unavailable','items':[{'name':'Reason','value':f'No ETF flow feed for {selected_symbol or "N/A"}; switch to BTC or ETH to see live ETF flow.'}]}
    source_name, source_url, asset = next(s for s in sources if s[2] == selected_symbol)
    try:
        txt = http_text(source_url, cache_key='etf:'+source_url, cache_ttl=300, timeout=8)
        low = txt.lower()
        nums = []
        for m in re.finditer(r'([+-]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(m|mn|million|b|bn|billion)?', low):
            num, unit = m.group(1), m.group(2) or ''
            try:
                v = float(num.replace(',',''))
                if unit in ('m','mn','million'): v *= 1_000_000
                elif unit in ('b','bn','billion'): v *= 1_000_000_000
                nums.append(v)
            except Exception: pass
        if 'net outflow' in low or 'outflows' in low or ('outflow' in low and 'inflow' not in low):
            direction = 'Net outflow'
        elif 'net inflow' in low or 'inflows' in low or ('inflow' in low and 'outflow' not in low):
            direction = 'Net inflow'
        else:
            direction = 'Neutral'
        net = None
        if nums:
            net = max(nums)
            if direction == 'Net outflow': net = -net
        items = [{'name':'Asset','value':asset},{'name':'Direction','value':direction},{'name':'Latest net flow','value':f'${abs(net):,.0f}' if net is not None else 'N/A'},{'name':'Source','value':source_name}]
        if net is not None: items.append({'name':'Impact','value':'Bullish' if net > 0 else 'Bearish'})
        return {'status':'Live','netflow_usd':net,'direction':direction,'source':source_name,'items':items}
    except Exception as exc:
        return {'status':'Unavailable','netflow_usd':None,'direction':'Unknown','source':source_name,'items':[{'name':'Error','value':str(exc)}]}

def get_coin_sentiment(coin_id):
    try:
        data = coingecko_get(f'/coins/{coin_id}', {'localization':'false','tickers':'false','market_data':'false','community_data':'false','developer_data':'false','sparkline':'false'}, cache_ttl=120)
        up = data.get('sentiment_votes_up_percentage'); down = data.get('sentiment_votes_down_percentage')
        if up is None or down is None: return None
        score = float(up); label = 'Greed' if score >= 65 else 'Neutral' if score >= 40 else 'Fear'
        return {'score':score,'label':label,'source':'CoinGecko token sentiment','drivers':[{'name':'Positive sentiment','value':f'{up:.1f}%'},{'name':'Negative sentiment','value':f'{down:.1f}%'}]}
    except Exception: return None

def get_btc_dominance():
    try:
        data = coingecko_get('/global', cache_ttl=120)
        btc = float(data.get('data',{}).get('market_cap_percentage',{}).get('btc') or 0)
        return {'value': btc, 'source': 'CoinGecko global market data'}
    except Exception: return {'value': None, 'source': 'Unavailable'}

def _pct_change(old, new):
    if old in (None, 0) or new is None: return 0.0
    return ((new - old) / old) * 100.0

def _score_from_range(value, low, high, invert=False):
    if value is None: return 50.0
    if high == low: return 50.0
    x = max(0.0, min(1.0, (float(value) - float(low)) / (float(high) - float(low))))
    s = x * 100.0
    return 100.0 - s if invert else s

def get_google_trends_proxy():
    result = []
    for term in ['bitcoin','bitcoin scam','bitcoin price manipulation']:
        try:
            txt = http_text(f'https://www.google.com/search?q={urllib.parse.quote(term)}', cache_key='gtr:'+term, cache_ttl=1800, timeout=6)
            result.append({'term':term,'hit':len(txt)})
        except Exception: result.append({'term':term,'hit':None})
    return result

def get_social_proxy(coin_id):
    try:
        data = coingecko_get(f'/coins/{coin_id}', {'localization':'false','tickers':'false','market_data':'false','community_data':'true','developer_data':'false','sparkline':'false'}, cache_ttl=120)
        cc = data.get('community_data') or {}
        twitter = float(cc.get('twitter_followers') or 0); reddit = float(cc.get('reddit_subscribers') or 0); telegram = float(cc.get('telegram_channel_user_count') or 0)
        raw = (math.log10(1+twitter)*35.0) + (math.log10(1+reddit)*35.0) + (math.log10(1+telegram)*30.0)
        return {'score': max(0.0, min(100.0, raw)), 'source': 'CoinGecko community data'}
    except Exception: return {'score':50.0,'source':'Unavailable'}

def get_market_momentum_volume(coin_id, meta):
    history = get_coin_history(coin_id); prices = history.get('prices',[]) if isinstance(history,dict) else []
    chart = [{'ts':int(r[0]),'price':float(r[1])} for r in prices if len(r)>=2]
    closes = [p['price'] for p in chart]
    volumes = history.get('total_volumes',[]) if isinstance(history,dict) else []
    volseries = [float(r[1]) for r in volumes if len(r)>=2]
    cp = float(meta.get('current_price') or 0); cv = float(meta.get('total_volume') or 0)
    mom30=mom90=vol30=vol90=None
    if len(closes)>=90:
        mom30=_pct_change(sum(closes[-30:])/30.0,cp); mom90=_pct_change(sum(closes[-90:])/90.0,cp)
    if len(volseries)>=90:
        vol30=_pct_change(sum(volseries[-30:])/30.0,cv); vol90=_pct_change(sum(volseries[-90:])/90.0,cv)
    return {'momentum_score':_score_from_range((mom30 or 0)*0.6+(mom90 or 0)*0.4,-20,20),'volume_score':_score_from_range((vol30 or 0)*0.6+(vol90 or 0)*0.4,-20,20),'mom30':mom30,'mom90':mom90,'vol30':vol30,'vol90':vol90,'chart':chart}

def get_volatility_score(chart):
    closes = [p['price'] for p in chart if p.get('price') is not None]
    if len(closes)<90: return {'score':50.0,'source':'Insufficient history','vol30':None,'vol90':None}
    def stdev(seq):
        m=sum(seq)/len(seq); return math.sqrt(sum((x-m)**2 for x in seq)/len(seq))
    last30=closes[-30:]; last90=closes[-90:]
    avg30=sum(last30)/30.0; avg90=sum(last90)/90.0
    cv30=(stdev(last30)/avg30)*100.0 if avg30 else 0.0; cv90=(stdev(last90)/avg90)*100.0 if avg90 else 0.0
    score=max(0.0,min(100.0,100.0-min(100.0,abs(cv30-cv90)*6.0)))
    return {'score':score,'source':'30d/90d volatility','vol30':cv30,'vol90':cv90}

def get_btc_dominance_score():
    try:
        data=coingecko_get('/global',cache_ttl=120)
        btc=float(data.get('data',{}).get('market_cap_percentage',{}).get('btc') or 0)
        return btc, {'value':btc,'source':'CoinGecko global market data'}
    except Exception: return None, {'value':None,'source':'Unavailable'}

def dominance_score_from_btc(btc_dom):
    if btc_dom is None: return 50.0
    return max(0.0, min(100.0, float(btc_dom)))

def build_composite_sentiment(coin_id, meta, chart, options=None):
    vol=get_volatility_score(chart); momentum=get_market_momentum_volume(coin_id, meta)
    social=get_social_proxy(coin_id); btc_dom,_=get_btc_dominance_score(); trends=get_google_trends_proxy()
    trend_score=50.0; trend_hits=[t['hit'] for t in trends if t.get('hit') is not None]
    if trend_hits:
        max_hit=max(trend_hits); min_hit=min(trend_hits)
        if max_hit>min_hit:
            fear_terms=sum(1 for t in trends if t['term']!='bitcoin' and t.get('hit')==max_hit)
            trend_score=max(0.0,min(100.0,50.0+(fear_terms*-12.0)+(max_hit-min_hit)/max(max_hit,1)*20.0))
    if any(t.get('term')=='bitcoin' and t.get('hit')==max(trend_hits,default=0) for t in trends):
        trend_score=max(trend_score,55.0)
    btc_dom_score=dominance_score_from_btc(btc_dom)
    btc_dominance_penalty=max(0.0, btc_dom_score-52.0)*0.9
    crypto_param=max(0.0,min(100.0,(vol['score']*0.16)+(((momentum['momentum_score']+momentum['volume_score'])/2.0)*0.20)+(social['score']*0.12)+(trend_score*0.12)+(btc_dom_score*0.10)))
    score=(vol['score']*0.16)+((momentum['momentum_score']+momentum['volume_score'])/2.0*0.20)+(social['score']*0.12)+(btc_dom_score*0.12)+(trend_score*0.10)+(crypto_param*0.30)
    score-=btc_dominance_penalty
    if btc_dom is not None: score+=(btc_dom-50.0)*0.02
    if momentum['mom30'] is not None:
        if momentum['mom30']<-8: score-=7
        elif momentum['mom30']<0: score-=3
    if momentum['vol30'] is not None:
        if momentum['vol30']<-8: score-=5
        elif momentum['vol30']<0: score-=2
    if vol['vol30'] is not None and vol['vol90'] is not None and vol['vol30']>vol['vol90']*1.5: score-=4
    score=max(0.0,min(100.0,score))
    label='Extreme Greed' if score>=80 else 'Greed' if score>=60 else 'Neutral' if score>=40 else 'Fear' if score>=20 else 'Extreme Fear'
    drivers=[
        {'name':'Volatility (30d vs 90d)','value':f'{vol["score"]:.1f} | {vol.get("vol30") or 0:.2f}% / {vol.get("vol90") or 0:.2f}%'},
        {'name':'Momentum (30d vs 90d)','value':f'{momentum["momentum_score"]:.1f} | {momentum.get("mom30") or 0:+.2f}% / {momentum.get("mom90") or 0:+.2f}%'},
        {'name':'Volume (30d vs 90d)','value':f'{momentum["volume_score"]:.1f} | {momentum.get("vol30") or 0:+.2f}% / {momentum.get("vol90") or 0:+.2f}%'},
        {'name':'Social','value':f'{social["score"]:.1f}'},
        {'name':'BTC dominance','value':f'{btc_dom:.2f}%' if btc_dom is not None else 'N/A'},
        {'name':'BTC dominance penalty','value':f'-{btc_dominance_penalty:.1f}' if btc_dominance_penalty>0 else '0.0'},
        {'name':'Google Trends','value':f'{trend_score:.1f}'},
        {'name':'Crypto param (30%)','value':f'{crypto_param:.1f}'},
        {'name':'Final score','value':f'{score:.2f}'},
    ]
    return {'score':round(score,2),'label':label,'source':'Composite fear-greed model','drivers':drivers}

def query_llm(messages):
    if not LLM_ENABLED or not LLM_API_KEY: return None
    payload=json.dumps({'model':LLM_MODEL,'messages':messages,'temperature':0.7,'max_tokens':500}).encode('utf-8')
    req=Request(LLM_ENDPOINT,data=payload,headers={'Content-Type':'application/json','Authorization':f'Bearer {LLM_API_KEY}'})
    try:
        with urlopen(req,timeout=20) as r:
            data=json.loads(r.read().decode('utf-8')); choices=data.get('choices') or []
            return choices[0].get('message',{}).get('content') if choices else None
    except Exception: return None

def sma(values, n):
    out = []
    for i in range(len(values)):
        out.append(None if i+1<n else sum(values[i+1-n:i+1])/n)
    return out

def ema(values, n):
    out = []; 
    if not values: return out
    k=2/(n+1); prev=values[0]
    for v in values:
        prev=v*k+prev*(1-k); out.append(prev)
    return out

def rsi(values, period=14):
    if len(values)<period+1: return None
    gains=[]; losses=[]
    for i in range(1,period+1):
        d=values[i]-values[i-1]; gains.append(max(d,0)); losses.append(abs(min(d,0)))
    ag=sum(gains)/period; al=sum(losses)/period
    for i in range(period+1,len(values)):
        d=values[i]-values[i-1]; g=max(d,0); l=abs(min(d,0))
        ag=((ag*(period-1))+g)/period; al=((al*(period-1))+l)/period
    if al==0: return 100.0
    return 100-(100/(1+ag/al))

def build_options_data(meta, enabled=True):
    symbol=normalize_symbol(meta.get('symbol'))
    if not enabled: return {'available':False,'message':'Calls hidden.','calls_open':0,'puts_open':0,'put_call_ratio':None,'contracts':0,'source':'Paused'}
    deribit_ccy=meta.get('deribit')
    if not deribit_ccy: return {'available':False,'message':f'No options data for {symbol}.','calls_open':0,'puts_open':0,'put_call_ratio':None,'contracts':0,'source':'Deribit public API'}
    try:
        instruments=deribit_get('get_instruments',{'currency':deribit_ccy,'kind':'option','expired':'false'},cache_ttl=90)
        result=instruments.get('result',[]) if isinstance(instruments,dict) else []
        if not result: return {'available':False,'message':f'No instruments for {deribit_ccy}.','calls_open':0,'puts_open':0,'put_call_ratio':None,'contracts':0,'source':'Deribit public API'}
        calls_open=puts_open=call_count=put_count=0.0
        for item in result[:800]:
            oi=float(item.get('open_interest') or 0); ot=(item.get('option_type') or '').lower()
            if ot=='call': calls_open+=oi; call_count+=1
            elif ot=='put': puts_open+=oi; put_count+=1
        ratio=round(puts_open/calls_open,4) if calls_open else None
        return {'available':True,'message':None,'calls_open':round(calls_open,4),'puts_open':round(puts_open,4),'put_call_ratio':ratio,'contracts':int(call_count+put_count),'source':'Deribit public API'}
    except Exception as exc: return {'available':False,'message':f'Options API error: {exc}','calls_open':0,'puts_open':0,'put_call_ratio':None,'contracts':0,'source':'Deribit public API'}

def build_averages(chart):
    now_ms=int(time.time()*1000); out={}
    for label, days in {'1W':7,'1M':30,'1Y':365}.items():
        cutoff=now_ms-days*86400*1000; subset=[p['price'] for p in chart if p['ts']>=cutoff]
        if subset: out[label]={'avg':sum(subset)/len(subset),'min':min(subset),'max':max(subset)}
    return out

def build_coindata(coin_id, options_enabled=True):
    load_top_coins()
    meta=TOP_COINS_MAP.get(coin_id)
    if not meta: return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        fh=ex.submit(get_coin_history,coin_id); fi=ex.submit(get_inr_rate)
        fg=ex.submit(get_global_sentiment); ft=ex.submit(get_coin_sentiment,coin_id)
        fo=ex.submit(build_options_data,meta,options_enabled)
        ff=ex.submit(get_etf_netflow,normalize_symbol(meta.get('symbol')))
        history=fh.result(); inr_rate=fi.result(); global_sentiment=fg.result()
        token_sentiment=ft.result(); options=fo.result(); etf=ff.result()
    prices=history.get('prices',[]) if isinstance(history,dict) else []
    chart=[{'ts':int(r[0]),'price':float(r[1])} for r in prices if len(r)>=2]
    closes=[p['price'] for p in chart]
    usd_price=float(meta.get('current_price') or 0); market_cap_usd=float(meta.get('market_cap') or 0); volume_usd=float(meta.get('total_volume') or 0)
    supply={'circulating_supply':meta.get('circulating_supply'),'total_supply':meta.get('total_supply'),'max_supply':meta.get('max_supply'),'market_cap_usd':market_cap_usd,'market_cap_inr':market_cap_usd*inr_rate if market_cap_usd else 0,'volume_usd':volume_usd,'volume_inr':volume_usd*inr_rate if volume_usd else 0}
    price={'usd':usd_price,'inr':usd_price*inr_rate if usd_price else 0,'usd_24h_change':float(meta.get('price_change_percentage_24h') or 0),'usd_market_cap':supply['market_cap_usd'],'inr_market_cap':supply['market_cap_inr'],'usd_24h_vol':supply['volume_usd'],'inr_24h_vol':supply['volume_inr']}
    sentiment=build_composite_sentiment(coin_id,meta,chart,options=options)
    fng={'score':sentiment['score'],'label':sentiment['label'],'source':sentiment['source'],'drivers':sentiment['drivers']}
    return {
        'coin':{'id':meta.get('id'),'symbol':normalize_symbol(meta.get('symbol')),'name':meta.get('name') or coin_id},
        'price':price,'chart':chart,'averages':build_averages(chart),'usd_inr_rate':inr_rate,
        'ta':{'sma20':sma(closes,20),'sma50':sma(closes,50),'ema20':ema(closes,20),'rsi14':rsi(closes,14)},
        'supply':supply,'sentiment':sentiment,'fng':fng,'options':options,
        'btc_dominance':get_btc_dominance(),'overall_fng':get_overall_crypto_sentiment(),
        'etf_flow':etf,'sentiment_debug':{'global_fng':global_sentiment,'btc_dominance':None}
    }

def mybuddy_reply(coin_id, question, meta, price, sentiment):
    if not LLM_ENABLED: return {'reply':'MyBuddy disabled (LLM_ENABLED=0).','source':'Disabled','model':None}
    answer=query_llm([{'role':'system','content':'You are MyBuddy, a crypto research assistant. Be concise. This is not financial advice.'},{'role':'user','content':f"Analyze {meta.get('name')} ({normalize_symbol(meta.get('symbol'))}) at ${price.get('usd'):.4f}, 24h {price.get('usd_24h_change'):+.2f}%, sentiment {sentiment.get('score'):.1f}. Question: {question}"}])
    return {'reply':answer.strip(),'source':'LLM','model':LLM_MODEL} if answer else {'reply':'LLM_API_KEY missing or unreachable.','source':'Fallback','model':None}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): return

    def _json(self, obj):
        body=json.dumps(obj).encode('utf-8')
        try:
            self.send_response(200); self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Content-Length',str(len(body)))
            self.end_headers(); self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _file(self, path, ctype):
        with open(path,'rb') as f: body=f.read()
        self.send_response(200); self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed=urllib.parse.urlparse(self.path); path=parsed.path.rstrip('/') or '/'
        qs=urllib.parse.parse_qs(parsed.query)
        if path=='/': return self._file(HTML_FILE,'text/html; charset=utf-8')
        if path=='/api/coins':
            load_top_coins(); page=int(qs.get('page',['1'])[0]); per_page=int(qs.get('per_page',['100'])[0])
            start=(page-1)*per_page; end=start+per_page
            return self._json([{'id':c['id'],'symbol':normalize_symbol(c.get('symbol')),'name':c.get('name')} for c in TOP_COINS[start:end]])
        if path=='/api/overallfng':
            return self._json(get_overall_crypto_sentiment())
        if path=='/api/etfnetflow':
            coin_id=qs.get('coin',['bitcoin'])[0]; load_top_coins()
            meta2=TOP_COINS_MAP.get(coin_id)
            sym2=normalize_symbol(meta2.get('symbol')) if meta2 else 'BTC'
            return self._json(get_etf_netflow(sym2))
        if path=='/api/coindata':
            coin_id=qs.get('coin',['bitcoin'])[0]; options_enabled=qs.get('options',['1'])[0]=='1'
            try: data=build_coindata(coin_id,options_enabled=options_enabled)
            except Exception as exc: data={'error':str(exc)}
            return self._json(data or {'error':f'Unknown coin: {coin_id}'})
        if path=='/api/mybuddy':
            coin_id=qs.get('coin',['bitcoin'])[0]; question=qs.get('q',[''])[0].strip()
            if not question: return self._json({'error':'Question parameter is required.'})
            try:
                load_top_coins(); meta=TOP_COINS_MAP.get(coin_id)
                if not meta: raise ValueError(f'Unknown coin: {coin_id}')
                price={'usd':float(meta.get('current_price') or 0),'usd_24h_change':float(meta.get('price_change_percentage_24h') or 0),'usd_market_cap':float(meta.get('market_cap') or 0)}
                sentiment=build_composite_sentiment(coin_id,meta,[],options=None)
                return self._json(mybuddy_reply(coin_id,question,meta,price,sentiment))
            except Exception as exc: return self._json({'error':str(exc)})
        if path.startswith('/api/'):
            body=json.dumps({'error':f'Not found: {path}'}).encode('utf-8')
            self.send_response(404); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        self.send_error(404)


def main():
    load_top_coins(force=True)
    print('\n'+'='*60+'\n CryptoLens - Updated Edition\n'+'='*60)
    print(f' URL: http://localhost:{PORT}\n Coins: {len(TOP_COINS)}\n'+'='*60+'\n')
    socketserver.TCPServer.allow_reuse_address=True
    with socketserver.TCPServer(('',PORT),Handler) as server:
        def opener():
            time.sleep(1.2)
            try: webbrowser.open(f'http://localhost:{PORT}')
            except Exception: pass
        threading.Thread(target=opener,daemon=True).start()
        try: server.serve_forever()
        except KeyboardInterrupt: print('\nServer stopped.\n')

if __name__=='__main__':
    main()