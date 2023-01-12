import numpy as np
import pytest

from pywr.recorders import (
    NumpyArrayStorageRecorder
)

from hydra_pywr.nodes.hydropower import *
from hydra_pywr.runner import PywrFileRunner
from fixtures import (
    model_directory,
    hydropower_verification_model
)


class TestResults():
    def test_hydropower_results(self, hydropower_verification_model):
        runner = PywrFileRunner()
        runner.load_pywr_model_from_file(hydropower_verification_model)
        gerd_rec = NumpyArrayStorageRecorder(runner.model, runner.model.nodes["GERD"])
        runner.run_pywr_model()

        gerd_st_df = gerd_rec.to_dataframe()
        gerd_turbine_df = runner.model.recorders["hydropowerrecorder.GERD_turbine"].to_dataframe()

        """
          Verified values for start and end sequences of results period.
          Keys must match f"{}_df" var names, values are dicts of
          slice-as-tuple: np.array expected values.
        """
        expected = {
            "gerd_st": {
                (0,5): np.array([[46360.63694919],
                                 [42965.88915086],
                                 [39208.38512899],
                                 [35785.00239353],
                                 [32702.58927283]]),

                (-5,None): np.array([[40747.57945841],
                                     [49263.9095238 ],
                                     [53011.73528208],
                                     [53127.665874  ],
                                     [50834.14051615]])
            },
            "gerd_turbine": {
                (0,5): np.array([[1730.1752206 ],
                                 [1595.41837885],
                                 [1466.1889006 ],
                                 [1329.45736668],
                                 [1210.65347216]]),

                (-5,None): np.array([[1070.2307505 ],
                                     [1384.66567242],
                                     [1706.67934078],
                                     [1827.94504144],
                                     [1831.7610369 ]])
            }
        }

        assert gerd_st_df.shape == (240, 1)
        assert gerd_turbine_df.shape == (240, 1)

        for elem, ranges in expected.items():
            for s,v in ranges.items():
                df = locals()[f"{elem}_df"]
                assert np.all(np.isclose(df[slice(*s)].values, v))
