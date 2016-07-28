from __future__ import division

from nose_parameterized import parameterized
from six.moves import range
import numpy as np
import pandas as pd
import talib

from zipline.lib.adjusted_array import AdjustedArray
from zipline.pipeline import TermGraph
from zipline.pipeline.data import USEquityPricing
from zipline.pipeline.engine import SimplePipelineEngine
from zipline.pipeline.term import AssetExists
from zipline.pipeline.factors import (
    BollingerBands,
    Aroon,
    FastStochasticOscillator,
    IchimokuKinkoHyo,
    LinearWeightedMovingAverage,
    RateOfChangePercentage,
    TrueRange,
)
from zipline.testing import ExplodingObject, parameter_space
from zipline.testing.fixtures import WithAssetFinder, ZiplineTestCase
from zipline.testing.predicates import assert_equal


class WithTechnicalFactor(WithAssetFinder):
    """ZiplineTestCase fixture for testing technical factors.
    """
    ASSET_FINDER_EQUITY_SIDS = tuple(range(5))
    START_DATE = pd.Timestamp('2014-01-01', tz='utc')

    @classmethod
    def init_class_fixtures(cls):
        super(WithTechnicalFactor, cls).init_class_fixtures()
        cls.ndays = ndays = 24
        cls.nassets = nassets = len(cls.ASSET_FINDER_EQUITY_SIDS)
        cls.dates = dates = pd.date_range(cls.START_DATE, periods=ndays)
        cls.assets = pd.Index(cls.asset_finder.sids)
        cls.engine = SimplePipelineEngine(
            lambda column: ExplodingObject(),
            dates,
            cls.asset_finder,
        )
        cls.asset_exists = exists = np.full((ndays, nassets), True, dtype=bool)
        cls.asset_exists_masked = masked = exists.copy()
        masked[:, -1] = False

    def run_graph(self, graph, initial_workspace, mask_sid):
        initial_workspace.setdefault(
            AssetExists(),
            self.asset_exists_masked if mask_sid else self.asset_exists,
        )
        return self.engine.compute_chunk(
            graph,
            self.dates,
            self.assets,
            initial_workspace,
        )


class BollingerBandsTestCase(WithTechnicalFactor, ZiplineTestCase):
    @classmethod
    def init_class_fixtures(cls):
        super(BollingerBandsTestCase, cls).init_class_fixtures()
        cls._closes = closes = (
            np.arange(cls.ndays, dtype=float)[:, np.newaxis] +
            np.arange(cls.nassets, dtype=float) * 100
        )
        cls._closes_masked = masked = closes.copy()
        masked[:, -1] = np.nan

    def closes(self, masked):
        return self._closes_masked if masked else self._closes

    def expected(self, window_length, k, closes):
        """Compute the expected data (without adjustments) for the given
        window, k, and closes array.

        This uses talib.BBANDS to generate the expected data.
        """
        lower_cols = []
        middle_cols = []
        upper_cols = []
        for n in range(self.nassets):
            close_col = closes[:, n]
            if np.isnan(close_col).all():
                # ta-lib doesn't deal well with all nans.
                upper, middle, lower = [np.full(self.ndays, np.nan)] * 3
            else:
                upper, middle, lower = talib.BBANDS(
                    close_col,
                    window_length,
                    k,
                    k,
                )

            upper_cols.append(upper)
            middle_cols.append(middle)
            lower_cols.append(lower)

        # Stack all of our uppers, middles, lowers into three 2d arrays
        # whose columns are the sids. After that, slice off only the
        # rows we care about.
        where = np.s_[window_length - 1:]
        uppers = np.column_stack(upper_cols)[where]
        middles = np.column_stack(middle_cols)[where]
        lowers = np.column_stack(lower_cols)[where]
        return uppers, middles, lowers

    @parameter_space(
        window_length={5, 10, 20},
        k={1.5, 2, 2.5},
        mask_sid={True, False},
    )
    def test_bollinger_bands(self, window_length, k, mask_sid):
        closes = self.closes(mask_sid)
        result = self.run_graph(
            TermGraph({
                'f': BollingerBands(
                    window_length=window_length,
                    k=k,
                ),
            }),
            initial_workspace={
                USEquityPricing.close: AdjustedArray(
                    closes,
                    np.full_like(closes, True, dtype=bool),
                    {},
                    np.nan,
                ),
            },
            mask_sid=mask_sid,
        )['f']

        expected_upper, expected_middle, expected_lower = self.expected(
            window_length,
            k,
            closes,
        )

        assert_equal(result.upper, expected_upper)
        assert_equal(result.middle, expected_middle)
        assert_equal(result.lower, expected_lower)

    def test_bollinger_bands_output_ordering(self):
        bbands = BollingerBands(window_length=5, k=2)
        lower, middle, upper = bbands
        self.assertIs(lower, bbands.lower)
        self.assertIs(middle, bbands.middle)
        self.assertIs(upper, bbands.upper)


class AroonTestCase(ZiplineTestCase):
    window_length = 10
    nassets = 5
    dtype = [('down', 'f8'), ('up', 'f8')]

    @parameterized.expand([
        (np.arange(window_length),
         np.arange(window_length) + 1,
         np.recarray(shape=(nassets,), dtype=dtype,
                     buf=np.array([0, 100] * nassets, dtype='f8'))),
        (np.arange(window_length, 0, -1),
         np.arange(window_length, 0, -1) - 1,
         np.recarray(shape=(nassets,), dtype=dtype,
                     buf=np.array([100, 0] * nassets, dtype='f8'))),
        (np.array([10, 10, 10, 1, 10, 10, 10, 10, 10, 10]),
         np.array([1, 1, 1, 1, 1, 10, 1, 1, 1, 1]),
         np.recarray(shape=(nassets,), dtype=dtype,
                     buf=np.array([100 * 3 / 9, 100 * 5 / 9] * nassets,
                                  dtype='f8'))),
    ])
    def test_aroon_basic(self, lows, highs, expected_out):
        aroon = Aroon(window_length=self.window_length)
        today = pd.Timestamp('2014', tz='utc')
        assets = pd.Index(np.arange(self.nassets, dtype=np.int64))
        shape = (self.nassets,)
        out = np.recarray(shape=shape, dtype=self.dtype,
                          buf=np.empty(shape=shape, dtype=self.dtype))

        aroon.compute(today, assets, out, lows, highs)

        assert_equal(out, expected_out)


class TestFastStochasticOscillator(WithTechnicalFactor, ZiplineTestCase):
    """
    Test the Fast Stochastic Oscillator
    """

    def test_fso_expected_basic(self):
        """
        Simple test of expected output from fast stochastic oscillator
        """
        fso = FastStochasticOscillator()

        today = pd.Timestamp('2015')
        assets = np.arange(3, dtype=np.float64)
        out = np.empty(shape=(3,), dtype=np.float64)

        highs = np.full((50, 3), 3, dtype=np.float64)
        lows = np.full((50, 3), 2, dtype=np.float64)
        closes = np.full((50, 3), 4, dtype=np.float64)

        fso.compute(today, assets, out, closes, lows, highs)

        # Expected %K
        assert_equal(out, np.full((3,), 200, dtype=np.float64))

    def test_fso_expected_with_talib(self):
        """
        Test the output that is returned from the fast stochastic oscillator
        is the same as that from the ta-lib STOCHF function.
        """
        window_length = 14
        nassets = 6
        closes = np.random.random_integers(1, 6, size=(50, nassets))*1.0
        highs = np.random.random_integers(4, 6, size=(50, nassets))*1.0
        lows = np.random.random_integers(1, 3, size=(50, nassets))*1.0

        expected_out_k = []
        for i in range(nassets):
            e = talib.STOCHF(
                high=highs[:, i],
                low=lows[:, i],
                close=closes[:, i],
                fastk_period=window_length,
            )

            expected_out_k.append(e[0][-1])
        expected_out_k = np.array(expected_out_k)

        today = pd.Timestamp('2015')
        out = np.empty(shape=(nassets,), dtype=np.float)
        assets = np.arange(nassets, dtype=np.float)

        fso = FastStochasticOscillator()
        fso.compute(
            today, assets, out, closes, lows, highs
        )

        assert_equal(out, expected_out_k)


class IchimokuKinkoHyoTestCase(ZiplineTestCase):
    def test_ichimoku_kinko_hyo(self):
        window_length = 52
        today = pd.Timestamp('2014', tz='utc')
        nassets = 5
        assets = pd.Index(np.arange(nassets))
        days_col = np.arange(window_length)[:, np.newaxis]
        highs = np.arange(nassets) + 2 + days_col
        closes = np.arange(nassets) + 1 + days_col
        lows = np.arange(nassets) + days_col

        tenkan_sen_length = 9
        kijun_sen_length = 26
        chikou_span_length = 26
        ichimoku_kinko_hyo = IchimokuKinkoHyo(
            window_length=window_length,
            tenkan_sen_length=tenkan_sen_length,
            kijun_sen_length=kijun_sen_length,
            chikou_span_length=chikou_span_length,
        )

        dtype = [
            ('tenkan_sen', 'f8'),
            ('kijun_sen', 'f8'),
            ('senkou_span_a', 'f8'),
            ('senkou_span_b', 'f8'),
            ('chikou_span', 'f8'),
        ]
        out = np.recarray(
            shape=(nassets,),
            dtype=dtype,
            buf=np.empty(shape=(nassets,), dtype=dtype),
        )
        ichimoku_kinko_hyo.compute(
            today,
            assets,
            out,
            highs,
            lows,
            closes,
            tenkan_sen_length,
            kijun_sen_length,
            chikou_span_length,
        )

        expected_tenkan_sen = np.array([
            (53 + 43) / 2,
            (54 + 44) / 2,
            (55 + 45) / 2,
            (56 + 46) / 2,
            (57 + 47) / 2,
        ])
        expected_kijun_sen = np.array([
            (53 + 26) / 2,
            (54 + 27) / 2,
            (55 + 28) / 2,
            (56 + 29) / 2,
            (57 + 30) / 2,
        ])
        expected_senkou_span_a = (expected_tenkan_sen + expected_kijun_sen) / 2
        expected_senkou_span_b = np.array([
            (53 + 0) / 2,
            (54 + 1) / 2,
            (55 + 2) / 2,
            (56 + 3) / 2,
            (57 + 4) / 2,
        ])
        expected_chikou_span = np.array([
            27.0,
            28.0,
            29.0,
            30.0,
            31.0,
        ])

        assert_equal(
            out.tenkan_sen,
            expected_tenkan_sen,
            msg='tenkan_sen',
        )
        assert_equal(
            out.kijun_sen,
            expected_kijun_sen,
            msg='kijun_sen',
        )
        assert_equal(
            out.senkou_span_a,
            expected_senkou_span_a,
            msg='senkou_span_a',
        )
        assert_equal(
            out.senkou_span_b,
            expected_senkou_span_b,
            msg='senkou_span_b',
        )
        assert_equal(
            out.chikou_span,
            expected_chikou_span,
            msg='chikou_span',
        )

    @parameter_space(
        arg={'tenkan_sen_length', 'kijun_sen_length', 'chikou_span_length'},
    )
    def test_input_validation(self, arg):
        window_length = 52

        with self.assertRaises(ValueError) as e:
            IchimokuKinkoHyo(**{arg: window_length + 1})

        assert_equal(
            str(e.exception),
            '%s must be <= the window_length: 53 > 52' % arg,
        )


class TestRateOfChangePercentage(ZiplineTestCase):
    @parameterized.expand([
        ('constant', [2.] * 10, 0.0),
        ('step', [2.] + [1.] * 9, -50.0),
        ('linear', [2. + x for x in range(10)], 450.0),
        ('quadratic', [2. + x**2 for x in range(10)], 4050.0),
    ])
    def test_rate_of_change_percentage(self, test_name, data, expected):
        window_length = len(data)

        rocp = RateOfChangePercentage(
            inputs=(USEquityPricing.close,),
            window_length=window_length,
        )
        today = pd.Timestamp('2014')
        assets = np.arange(5, dtype=np.int64)
        # broadcast data across assets
        data = np.array(data)[:, np.newaxis] * np.ones(len(assets))

        out = np.zeros(len(assets))
        rocp.compute(today, assets, out, data)
        assert_equal(out, np.full((len(assets),), expected))


class TestLinearWeightedMovingAverage(ZiplineTestCase):
    def test_wma1(self):
        wma1 = LinearWeightedMovingAverage(
            inputs=(USEquityPricing.close,),
            window_length=10
        )

        today = pd.Timestamp('2015')
        assets = np.arange(5, dtype=np.int64)

        data = np.ones((10, 5))
        out = np.zeros(data.shape[1])

        wma1.compute(today, assets, out, data)
        assert_equal(out, np.ones(5))

    def test_wma2(self):
        wma2 = LinearWeightedMovingAverage(
            inputs=(USEquityPricing.close,),
            window_length=10
        )

        today = pd.Timestamp('2015')
        assets = np.arange(5, dtype=np.int64)

        data = np.arange(50, dtype=float).reshape((10, 5))
        out = np.zeros(data.shape[1])

        wma2.compute(today, assets, out, data)
        assert_equal(out, np.array([30.,  31.,  32.,  33.,  34.]))


class TestTrueRange(WithTechnicalFactor, ZiplineTestCase):

    def test_tr_basic(self):
        tr = TrueRange()

        today = pd.Timestamp('2014')
        assets = np.arange(3, dtype=np.int64)
        out = np.empty(3, dtype=np.float64)

        highs = np.full((2, 3), 3)
        lows = np.full((2, 3), 2)
        closes = np.full((2, 3), 1)

        tr.compute(today, assets, out, highs, lows, closes)
        assert_equal(out, np.full((3,), 2))
