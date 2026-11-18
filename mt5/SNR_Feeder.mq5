//+------------------------------------------------------------------+
//| SNR_Feeder.mq5                                                   |
//| Sends CLOSED candles (MN1, W1, D1, H4, H1, M15) to the analysis       |
//| backend. It never places, modifies or closes trades.             |
//+------------------------------------------------------------------+
#property copyright "SNR Story Line System"
#property version   "1.20"
#property description "Data feeder for the SNR story-line backend (read-only, no trading)."

input string BackendUrl   = "https://YOUR-SERVICE.onrender.com/ingest"; // must be whitelisted in MT5 options
input string ApiKey       = "";     // same value as API_KEY on Render
input int    HeartbeatSec = 15;     // keep-alive interval; also how fast "Sync now" in the web page reaches MT5
input int    BarsMN1      = 36;
input int    BarsW1       = 120;
input int    BarsD1       = 300;
input int    BarsH4       = 500;
input int    BarsH1       = 700;
input int    BarsM15      = 600;   // 15-min candles for Setup 4 (about 6 days)

ENUM_TIMEFRAMES g_tf[6]   = {PERIOD_MN1, PERIOD_W1, PERIOD_D1, PERIOD_H4, PERIOD_H1, PERIOD_M15};
string          g_name[6] = {"MN1", "W1", "D1", "H4", "H1", "M15"};
datetime        g_last[6];
bool            g_needFull = true;
datetime        g_lastSend = 0;
datetime        g_nextTry  = 0;

int BarsFor(const int i)
  {
   switch(i)
     {
      case 0: return BarsMN1;
      case 1: return BarsW1;
      case 2: return BarsD1;
      case 3: return BarsH4;
      case 4: return BarsH1;
      default: return BarsM15;
     }
  }

// closed candles only (shift 1 onward), oldest first: [[time,o,h,l,c],...]
string BarsJson(const int i, const int count)
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, g_tf[i], 1, count, r);
   if(got <= 0)
      return "";
   string s = "[";
   for(int k = 0; k < got; k++)
     {
      if(k > 0) s += ",";
      s += "[" + IntegerToString((long)r[k].time) + "," +
           DoubleToString(r[k].open,  _Digits) + "," +
           DoubleToString(r[k].high,  _Digits) + "," +
           DoubleToString(r[k].low,   _Digits) + "," +
           DoubleToString(r[k].close, _Digits) + "]";
     }
   return s + "]";
  }

string MetaJson(const bool full)
  {
   long off = (long)(TimeTradeServer() - TimeGMT());
   off = (long)(MathRound(off / 900.0) * 900.0);   // round to 15 min
   string s = "\"symbol\":\"" + _Symbol + "\"";
   s += ",\"server_time\":"  + IntegerToString((long)TimeTradeServer());
   s += ",\"gmt_offset\":"   + IntegerToString(off);
   s += ",\"digits\":"       + IntegerToString((int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   s += ",\"point\":"        + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_POINT), 8);
   s += ",\"tick_size\":"    + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), 8);
   s += ",\"tick_value\":"   + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), 8);
   s += ",\"vol_min\":"      + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 4);
   s += ",\"vol_step\":"     + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), 4);
   s += ",\"vol_max\":"      + DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), 4);
   s += ",\"balance\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   s += ",\"currency\":\""   + AccountInfoString(ACCOUNT_CURRENCY) + "\"";
   s += ",\"full\":"         + (full ? "true" : "false");
   return s;
  }

bool Post(const string json, string &resp)
  {
   char body[];
   int n = StringToCharArray(json, body, 0, StringLen(json), CP_UTF8);
   ArrayResize(body, n);
   char   res[];
   string rh;
   string hdr = "Content-Type: application/json\r\nX-API-Key: " + ApiKey + "\r\n";
   ResetLastError();
   int code = WebRequest("POST", BackendUrl, hdr, 30000, body, res, rh);
   if(code == -1)
     {
      PrintFormat("SNR_Feeder: WebRequest failed (err %d). Add the URL under Tools > Options > Expert Advisors > Allow WebRequest.", GetLastError());
      return false;
     }
   resp = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
   if(code != 200)
     {
      PrintFormat("SNR_Feeder: backend returned HTTP %d: %s", code, resp);
      return false;
     }
   return true;
  }

// full=true: send all history for all TFs. Otherwise send last 4 closed bars of TFs in mask.
bool Send(const bool full, const int mask)
  {
   string tfs = "";
   for(int i = 0; i < 6; i++)
     {
      if(!full && ((mask >> i) & 1) == 0)
         continue;
      string b = BarsJson(i, full ? BarsFor(i) : 4);
      if(b == "")
        {
         if(full && i == 5) continue;   // 15-min history is optional: do not block the full load
         if(full) { Print("SNR_Feeder: history for ", g_name[i], " not ready yet, retrying..."); return false; }
         continue;
        }
      if(tfs != "") tfs += ",";
      tfs += "\"" + g_name[i] + "\":" + b;
     }
   string json = "{" + MetaJson(full) + ",\"tfs\":{" + tfs + "}}";
   string resp;
   if(!Post(json, resp))
      return false;
   if(StringFind(resp, "\"need_full\":true") >= 0 || StringFind(resp, "\"sync\":true") >= 0)
      g_needFull = true;          // backend restarted / lacks data / you pressed "Sync now": resend everything
   else if(full)
      g_needFull = false;
   if(full)
      for(int i = 0; i < 6; i++)
         g_last[i] = iTime(_Symbol, g_tf[i], 1);
   return true;
  }

int OnInit()
  {
   if(StringLen(ApiKey) == 0)
     {
      Print("SNR_Feeder: set the ApiKey input.");
      return INIT_PARAMETERS_INCORRECT;
     }
   ArrayInitialize(g_last, 0);
   EventSetTimer(1);
   PrintFormat("SNR_Feeder started on %s. Sending to %s", _Symbol, BackendUrl);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   datetime now = TimeLocal();
   if(now < g_nextTry)
      return;

   if(g_needFull)
     {
      if(!Send(true, 0))
         g_nextTry = now + 15;
      else
         g_lastSend = now;
      return;
     }

   int mask = 0;
   for(int i = 0; i < 6; i++)
     {
      datetime t = iTime(_Symbol, g_tf[i], 1);
      if(t > 0 && t != g_last[i])
         mask |= (1 << i);
     }

   if(mask > 0)
     {
      if(Send(false, mask))
        {
         for(int i = 0; i < 6; i++)
            if(((mask >> i) & 1) == 1)
               g_last[i] = iTime(_Symbol, g_tf[i], 1);
         g_lastSend = now;
        }
      else
         g_nextTry = now + 10;
      return;
     }

   if(now - g_lastSend >= HeartbeatSec)
     {
      if(!Send(false, 0))
         g_nextTry = now + 10;
      g_lastSend = now;
     }
  }
