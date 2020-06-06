import numpy as np

from typing import *
from functools import partial
from cftool.misc import SavingMixin, get_unique_indices

from .types import *
from ..types import *


class SplitResult(NamedTuple):
    dataset: TabularDataset
    corresponding_indices: np.ndarray
    remaining_indices: np.ndarray


class DataSplitter(SavingMixin):
    """
    Util class for dividing dataset based on task type
    * If it's regression task, it's simple to split data
    * If it's classification task, we need to split data based on labels, because we need
    to ensure the divided data contain all labels available

    Examples
    --------
    See tests/unittests/tabular/test_utils.py

    """

    @property
    def data_tuple_base(self) -> Union[None, Type[NamedTuple]]:
        return

    @property
    def data_tuple_attributes(self) -> Union[None, List[str]]:
        return

    def __init__(self,
                 *,
                 time_series_config: dict = None,
                 shuffle: bool = True,
                 replace: bool = False,
                 verbose_level: int = 2):
        self._remained_indices = None
        self._time_indices_list = self._time_indices_list_in_use = None
        self._label_indices_list = self._label_indices_list_in_use = None
        self._time_series_config, self._time_series_sorting_indices = time_series_config, None
        self._shuffle, self._replace = shuffle, replace
        self._verbose_level = verbose_level
        if time_series_config is not None:
            if replace:
                raise ValueError("`replace` cannot be True when splitting time series dataset")
            self._id_column_setting = time_series_config.get("id_column")
            self._time_column_setting = time_series_config.get("time_column")
            if self._id_column_setting is None:
                raise ValueError("id_column should be provided in time_series_config")
            if self._time_column_setting is None:
                raise ValueError("time_column should be provided in time_series_config")
            self._id_column_is_int, self._time_column_is_int = map(
                lambda column: isinstance(column, int), [self._id_column_setting, self._time_column_setting])

    @property
    def x(self) -> np.ndarray:
        return self._x

    @property
    def y(self) -> np.ndarray:
        return self._y

    @property
    def id_column(self):
        return self._id_column

    @property
    def time_column(self):
        return self._time_column

    @property
    def is_time_series(self):
        return self._time_series_config is not None

    @property
    def sorting_indices(self):
        if not self.is_time_series:
            raise ValueError("sorting_indices should not be called when it is not time series condition")
        return self._time_series_sorting_indices

    @property
    def remained_indices(self):
        return self._remained_indices[::-1].copy()

    @property
    def remained_xy(self):
        indices = self.remained_indices
        return self._x[indices], self._y[indices]

    # reset methods

    def _reset_reg(self):
        n_data = len(self._x)
        if not self._shuffle:
            self._remained_indices = np.arange(n_data)
        else:
            self._remained_indices = np.random.permutation(n_data)
        self._remained_indices = self._remained_indices.astype(np_int_type)

    def _reset_clf(self):
        if self._label_indices_list is None:
            flattened_y = self._y.ravel()
            unique_indices = get_unique_indices(flattened_y)
            self._unique_labels, counts = unique_indices[:2]
            self._label_indices_list = unique_indices.split_indices
            self._n_samples = len(flattened_y)
            self._label_ratios = counts / self._n_samples
            self._n_unique_labels = len(self._unique_labels)
            if self._n_unique_labels == 1:
                raise ValueError("only 1 unique label is detected, which is invalid in classification task")
            self._unique_labels = self._unique_labels.astype(np_int_type)
            self._label_indices_list = list(map(partial(np.asarray, dtype=np_int_type), self._label_indices_list))
        self._reset_indices_list("label_indices_list")

    def _reset_time_series(self):
        if self._time_indices_list is None:
            self.log_msg(f"gathering time -> indices mapping", self.info_prefix, verbose_level=5)
            self._unique_times, times_counts, self._time_indices_list = map(
                lambda arr: arr[::-1],
                get_unique_indices(self._time_column)
            )
            self._times_counts_cumsum = np.cumsum(times_counts).astype(np_int_type)
            assert self._times_counts_cumsum[-1] == len(self._time_column)
            self._time_series_sorting_indices = np.hstack(self._time_indices_list[::-1]).astype(np_int_type)
            self._unique_times = self._unique_times.astype(np_int_type)
            self._time_indices_list = list(map(partial(np.asarray, dtype=np_int_type), self._time_indices_list))
        self._reset_indices_list("time_indices_list")
        self._times_counts_cumsum_in_use = self._times_counts_cumsum.copy()

    def _reset_indices_list(self, attr):
        self_attr = getattr(self, f"_{attr}")
        if self._shuffle:
            tuple(map(np.random.shuffle, self_attr))
        attr_in_use = f"_{attr}_in_use"
        setattr(self, attr_in_use, [arr.copy() for arr in self_attr])
        self._remained_indices = np.hstack(getattr(self, attr_in_use)).astype(np_int_type)

    # split methods

    def _split_reg(self, n: int):
        tgt_indices = self._remained_indices[-n:]
        n = min(n, len(self._remained_indices) - 1)
        if self._replace:
            np.random.shuffle(self._remained_indices)
        elif n > 0:
            self._remained_indices = self._remained_indices[:-n]
        return tgt_indices

    def _split_clf(self, n: int):
        if n < self._n_unique_labels:
            raise ValueError(
                f"at least {self._n_unique_labels} samples are required because "
                f"we have {self._n_unique_labels} unique labels"
            )
        pop_indices_list, tgt_indices_list = [], []
        n_samples_per_label = np.maximum(1, np.round(n * self._label_ratios).astype(np_int_type))
        # -n_unique_labels <= n_samples_exceeded <= n_unique_labels
        n_samples_exceeded = n_samples_per_label.sum() - n
        # adjust n_samples_per_label to make sure `n` samples are split out
        if n_samples_exceeded != 0:
            sign, n_samples_exceeded = np.sign(n_samples_exceeded), abs(n_samples_exceeded)
            chosen_indices = np.arange(self._n_unique_labels)[n_samples_per_label != 1]
            np.random.shuffle(chosen_indices)
            n_chosen_indices = len(chosen_indices)
            n_tile = int(np.ceil(n_samples_exceeded / n_chosen_indices))
            n_proceeded = 0
            for _ in range(n_tile - 1):
                n_samples_per_label[chosen_indices] -= sign
                n_proceeded += n_chosen_indices
            for idx in chosen_indices[:n_samples_exceeded - n_proceeded]:
                n_samples_per_label[idx] -= sign
        assert n_samples_per_label.sum() == n
        n_overlap = 0
        for indices, n_sample_per_label in zip(self._label_indices_list_in_use, n_samples_per_label):
            n_samples_in_use = len(indices)
            tgt_indices_list.append(indices[-n_sample_per_label:])
            if n_sample_per_label >= n_samples_in_use - 1:
                pop_indices_list.append([])
                n_overlap += n_sample_per_label
            else:
                pop_indices_list.append(np.arange(n_samples_in_use - n_sample_per_label, n_samples_in_use))
        tgt_indices = np.hstack(tgt_indices_list)
        if self._replace:
            tuple(map(np.random.shuffle, self._label_indices_list_in_use))
            self._remained_indices = np.hstack(self._label_indices_list_in_use)
        else:
            self._label_indices_list_in_use = list(map(
                lambda arr, pop_indices: np.delete(arr, pop_indices),
                self._label_indices_list_in_use, pop_indices_list
            ))
            remain_indices = np.hstack(self._label_indices_list_in_use)
            base = np.zeros(self._n_samples)
            base[tgt_indices] += 1
            base[remain_indices] += 1
            assert np.sum(base >= 2) <= n_overlap
            self._remained_indices = remain_indices
        return tgt_indices

    def _split_time_series(self, n: int):
        split_arg = np.argmax(self._times_counts_cumsum_in_use >= n)
        n_left = self._times_counts_cumsum_in_use[split_arg] - n
        if split_arg == 0:
            n_res, selected_indices = n, []
        else:
            n_res = n - self._times_counts_cumsum_in_use[split_arg - 1]
            selected_indices = self._time_indices_list_in_use[:split_arg]
            self._time_indices_list_in_use = self._time_indices_list_in_use[split_arg:]
            self._times_counts_cumsum_in_use = self._times_counts_cumsum_in_use[split_arg:]
        selected_indices.append(self._time_indices_list_in_use[0][:n_res])
        if n_left > 0:
            self._time_indices_list_in_use[0] = self._time_indices_list_in_use[0][n_res:]
        else:
            self._time_indices_list_in_use = self._time_indices_list_in_use[1:]
            self._times_counts_cumsum_in_use = self._times_counts_cumsum_in_use[1:]
        tgt_indices, self._remained_indices = map(np.hstack, [selected_indices, self._time_indices_list_in_use])
        self._times_counts_cumsum_in_use -= n
        return tgt_indices[::-1].copy()

    def fit(self,
            dataset: TabularDataset) -> "DataSplitter":
        self._dataset = dataset
        self._is_regression = dataset.task_type is TaskTypes.REGRESSION
        self._x = dataset.x
        self._y = dataset.y
        if not self.is_time_series:
            self._time_column = None
        else:
            if not self._id_column_is_int and not self._time_column_is_int:
                self._id_column, self._time_column = map(
                    np.asarray, [self._id_column_setting, self._time_column_setting])
            else:
                id_column, time_column = self._id_column_setting, self._time_column_setting
                error_msg_prefix = "id_column & time_column should both be int, but"
                if not self._id_column_is_int:
                    raise ValueError(f"{error_msg_prefix} id_column='{id_column}' found")
                if not self._time_column_is_int:
                    raise ValueError(f"{error_msg_prefix} time_column='{time_column}' found")
                if id_column < time_column:
                    id_first = True
                    split_list = [id_column, id_column + 1, time_column, time_column + 1]
                else:
                    id_first = False
                    split_list = [time_column, time_column + 1, id_column, id_column + 1]
                columns = np.split(self._x, split_list, axis=1)
                if id_first:
                    self._id_column, self._time_column = columns[1], columns[3]
                else:
                    self._id_column, self._time_column = columns[3], columns[1]
                self._x = np.hstack([columns[0], columns[2], columns[4]])
            self._id_column, self._time_column = map(np.ravel, [self._id_column, self._time_column])
        return self.reset()

    def reset(self) -> "DataSplitter":
        if self._time_column is not None:
            self._reset_time_series()
        elif self._is_regression:
            self._reset_reg()
        else:
            self._reset_clf()
        return self

    def split(self,
              n: Union[int, float]) -> SplitResult:
        error_msg = "please call 'reset' method before calling 'split' method"
        if self._is_regression and self._remained_indices is None:
            raise ValueError(error_msg)
        if not self._is_regression and self._label_indices_list_in_use is None:
            raise ValueError(error_msg)
        if n >= len(self._remained_indices):
            remained_x, remained_y = self.remained_xy
            return SplitResult(
                TabularDataset.from_xy(remained_x, remained_y, self._dataset.task_type),
                self._remained_indices, np.array([], np.int)
            )
        if n < 1.:
            n = int(len(self._x) * n)
        if self.is_time_series:
            split_method = self._split_time_series
        else:
            split_method = self._split_reg if self._is_regression else self._split_clf
        tgt_indices = split_method(n)
        assert len(tgt_indices) == n
        x_split, y_split = self._x[tgt_indices], self._y[tgt_indices]
        dataset_split = TabularDataset(x_split, y_split, *self._dataset[2:])
        return SplitResult(dataset_split, tgt_indices, self._remained_indices)

    def split_multiple(self,
                       n_list: List[Union[int, float]],
                       *,
                       return_remained: bool = False) -> List[SplitResult]:
        n_list = n_list.copy()
        n_total = len(self._x)
        if not all(n_ <= 1. for n_ in n_list):
            if any(n_ < 1. for n_ in n_list):
                raise ValueError("some of the elements in `n_list` (but not all) are less than 1")
            if return_remained:
                n_list.append(n_total - sum(n_list))
        else:
            ratio_sum = sum(n_list)
            if ratio_sum > 1.:
                raise ValueError("sum of `n_list` should not be greater than 1")
            if return_remained and ratio_sum == 1:
                raise ValueError("sum of `n_list` should be less than 1 "
                                 "when `return_remained` is True")
            n_selected = int(n_total * ratio_sum)
            n_list[:-1] = [int(n_total * ratio) for ratio in n_list[:-1]]
            n_list[-1] = n_selected - sum(n_list[:-1])
            if ratio_sum < 1.:
                n_list.append(n_total - n_selected)
        return list(map(self.split, n_list))


__all__ = ["SplitResult", "DataSplitter"]
