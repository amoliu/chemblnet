import tensorflow as tf
from tensorflow.python.framework.ops import colocate_with
import numpy as np

class PosteriorMean(object):
    """Class for computing posterior mean and variance."""
    def __init__(self):
        self.n = 0
        self.sample_avg = None
        self.sample_var = None

    def addSample(self, sample_new, average):
        if not average:
            self.n = 0
            self.sample_avg = np.array(sample_new)
            return
        ## averaging
        self.n += 1
        if self.n == 1:
            self.sample_avg = np.array(sample_new)
            self.sample_var = np.zeros(sample_new.shape)
            return
        delta = sample_new - self.sample_avg
        self.sample_avg += delta / self.n
        self.sample_var += delta * (sample_new - self.sample_avg)

    def getVar(self):
        return self.sample_var / (self.n - 1)

    def getMean(self):
        return self.sample_avg


class SGLD(tf.train.Optimizer):
    """ Following variable_clipping_optimizer.py in TF."""

    def __init__(self,
                 learning_rate,
                 use_locking=False,
                 name="SGLD"):
        super(SGLD, self).__init__(use_locking, name)
        self._opt = tf.train.GradientDescentOptimizer(learning_rate)
        self._learning_rate = learning_rate
        self._name = name
        self._use_locking = use_locking

    def compute_gradients(self, *args, **kwargs):
        return self._opt.compute_gradients(*args, **kwargs)

    def get_slot(self, *args, **kwargs):
        return self._opt.get_slot(*args, **kwargs)

    def get_slot_names(self, *args, **kwargs):
        return self._opt.get_slot_names(*args, **kwargs)

    def apply_gradients(self, grads_and_vars, global_step=None, name=None):
        with tf.name_scope(name, self._name) as name:
            update_op = self._opt.apply_gradients(
                              grads_and_vars, global_step=global_step)
            add_noise_ops = []
            with tf.control_dependencies([update_op]):
                for grad, var in grads_and_vars:
                    if grad is None:
                        continue
                    with tf.name_scope("sgld_noise_" + var.op.name):
                        if isinstance(grad, tf.Tensor):
                            add_noise_ops.append(self._noise_dense(var))
                        else:
                            add_noise_ops.append(self._noise_sparse(grad, var))
            ## running combined op
            return tf.group(*([update_op] + add_noise_ops), name=name)

    def _noise_dense(self, var):
        updated_var_value = var._ref()  # pylint: disable=protected-access
        noise = tf.random_normal(shape = tf.shape(var), stddev = tf.sqrt(2 * self._learning_rate))
        with colocate_with(var):
            return var.assign_add(noise, use_locking=self._use_locking)

    def _noise_sparse(self, grad, var):
        assert isinstance(grad, tf.IndexedSlices)

        noise = tf.random_normal(shape = tf.shape(grad.values), stddev = tf.sqrt(2 * self._learning_rate))
        noise_sparse = tf.IndexedSlices(noise, grad.indices, grad.dense_shape)

        with colocate_with(var):
            return var.scatter_sub(noise_sparse, use_locking=self._use_locking)

