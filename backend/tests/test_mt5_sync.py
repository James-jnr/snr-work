import os, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app import config, db

class MT5SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        config.DB_PATH=Path(self.tmp.name)/'test.db'
        db._ready=False
        db.init()

    def tearDown(self): self.tmp.cleanup()

    def test_positions_update_and_disappear_from_snapshot(self):
        db.sync_mt5('EURUSD',[{'ticket':11,'position_id':11,'symbol':'EURUSD','side':'buy','volume':.1,'sl':1.0,'tp':1.2}],[])
        self.assertEqual(len(db.mt5_snapshot('EURUSD')['positions']),1)
        db.sync_mt5('EURUSD',[],[])
        self.assertEqual(db.mt5_snapshot('EURUSD')['positions'],[])

    def test_partial_close_deals_are_grouped_as_one_position(self):
        now=int(datetime.now(timezone.utc).timestamp())
        deals=[
            {'ticket':1,'position_id':99,'symbol':'EURUSD','entry_type':'in','deal_time':now,'volume':.2,'profit':0},
            {'ticket':2,'position_id':99,'symbol':'EURUSD','entry_type':'out','deal_time':now+1,'volume':.1,'profit':5},
            {'ticket':3,'position_id':99,'symbol':'EURUSD','entry_type':'out','deal_time':now+2,'volume':.1,'profit':-5},
        ]
        db.sync_mt5('EURUSD',[],deals,now+2)
        summary=db.mt5_trade_summary('EURUSD')
        self.assertEqual(len(summary['closed_trades']),1)
        self.assertEqual(summary['closed_trades'][0]['outcome'],'breakeven')
        self.assertEqual(summary['trades_started_today'],1)

if __name__=='__main__': unittest.main()
