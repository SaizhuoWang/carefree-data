import numpy as np

from typing import Any
from typing import Dict
from typing import List
from typing import Union
from optbinning import OptimalBinning
from optbinning import ContinuousOptimalBinning
from optbinning import MulticlassOptimalBinning
from cftool.misc import shallow_copy_dict

from .base import BinResults
from .base import BinningBase
from ...misc import is_float
from ...misc import FeatureInfo


@BinningBase.register("opt")
class OptBinning(BinningBase):
    def __init__(self, labels: np.ndarray, config: Dict[str, Any]):
        super().__init__(labels, config)
        self.opt_config = config.setdefault("opt_config", {})

    def binning(
        self,
        info: FeatureInfo,
        sorted_counts: np.ndarray,
        values: Union[List[str], List[float]],
    ) -> BinResults:
        x = info.flat_arr
        y = self.labels.ravel()
        opt_config = shallow_copy_dict(self.opt_config)
        # x info
        if is_float(x.dtype):
            opt_config["dtype"] = "numerical"
            opt_config.setdefault("solver", "cp")
        else:
            opt_config["dtype"] = "categorical"
            opt_config.setdefault("solver", "mip")
            opt_config.setdefault("cat_cutoff", 0.1)
        # y info
        if is_float(y.dtype):
            opt_config.pop("solver")
            base = ContinuousOptimalBinning
        else:
            if y.max() == 1:
                base = OptimalBinning
            else:
                opt_config.pop("dtype")
                opt_config.pop("cat_cutoff", None)
                base = MulticlassOptimalBinning
        # core
        opt = base(**opt_config).fit(x, y)
        fused_indices = opt.transform(values, metric="indices")
        transformed_unique_values = sorted(set(fused_indices))
        return BinResults(fused_indices, values, transformed_unique_values)


__all__ = ["OptBinning"]
