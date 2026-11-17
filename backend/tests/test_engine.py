import unittest
from app.engine import Bar, Params, make_levels, scan
from app.analysis import lots_for

class SNRRulesTests(unittest.TestCase):
    def test_a_level_is_bullish_close_before_bearish(self):
        bars=[Bar(0,1,1.2,.9,1.1),Bar(3600,1.1,1.15,1.0,1.02)]
        levels=make_levels('H1',bars)
        self.assertEqual([(x.kind,x.price,x.role) for x in levels],[('A',1.1,'R')])

    def test_v_level_is_bearish_close_before_bullish(self):
        bars=[Bar(0,1.1,1.15,1.0,1.02),Bar(3600,1.02,1.2,1.0,1.15)]
        levels=make_levels('H1',bars)
        self.assertEqual([(x.kind,x.price,x.role) for x in levels],[('V',1.02,'S')])

    def test_ocl_uses_first_candle_close_at_start_of_run(self):
        bars=[Bar(0,1,1.2,.9,1.1),Bar(3600,1.1,1.3,1.0,1.2),Bar(7200,1.2,1.4,1.1,1.3)]
        levels=make_levels('H1',bars)
        oc=[x for x in levels if x.kind=='OC']
        self.assertEqual(len(oc),1)
        self.assertEqual(oc[0].price,1.1)

    def test_rejection_requires_touch_and_close_back_on_original_side(self):
        bars=[Bar(0,1.0,1.2,.9,1.1),Bar(3600,1.1,1.15,1.0,1.02),
              Bar(7200,1.02,1.13,1.0,1.05)]
        p=Params(.01,{'H1':2},{'H1':2},0)
        rejections,_,_=scan('H1',bars,p)
        self.assertTrue(any(r.dir=='bear' and r.price==1.1 for r in rejections))

    def test_position_size_never_rounds_up_to_minimum_lot(self):
        meta={'balance':100,'tick_size':0.01,'tick_value':1,'vol_min':0.1,'vol_step':0.1,'vol_max':100}
        self.assertIsNone(lots_for(meta,1,0.2))

    def test_wick_break_does_not_flip_level(self):
        bars=[Bar(0,1.0,1.2,.9,1.1),Bar(3600,1.1,1.3,1.0,1.05),
              Bar(7200,1.05,1.15,1.0,1.04)]
        p=Params(.01,{'H1':2},{'H1':2},0)
        _,breaks,active=scan('H1',bars,p)
        self.assertEqual(breaks,[])
        self.assertTrue(any(x.kind=='A' and x.role=='R' for x in active))

if __name__=='__main__': unittest.main()
