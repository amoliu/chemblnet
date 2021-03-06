import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--reg",   type=float, help="regularization for layers", default = 1e-3)
parser.add_argument("--zreg",  type=float, help="regularization for Z (lookup table)", default = 1e-3)
parser.add_argument("--hsize", type=int,   help="size of the hidden layer", default = 100)
parser.add_argument("--side",  type=str,   help="side information", default = "chembl-IC50-compound-feat.mm")
parser.add_argument("--y",     type=str,   help="matrix", default = "chembl-IC50-346targets.mm")
parser.add_argument("--batch-size", type=int,   help="batch size", default = 100)
parser.add_argument("--epochs", type=int,  help="number of epochs", default = 200)
parser.add_argument("--test-ratio", type=float, help="ratio of y values to move to test set (default 0.20)", default=0.20)
parser.add_argument("--dropout", type=float, help="dropout keep probability (default None)", default=None)
parser.add_argument("--model", type=str,
                    help = "Network model",
                    choices = ["main", "linear", "non_linear_z", "residual", "residual2", "relu"],
                    default = "main")
parser.add_argument("--save",  type=str,   help="filename to save the model to (default)", default = None)

args = parser.parse_args()

import tensorflow as tf
import scipy.io
import numpy as np
import chemblnet as cn
from scipy.sparse import hstack

label = scipy.io.mmread(args.y)
X     = scipy.io.mmread(args.side).tocsr()

Ytrain, Ytest = cn.make_train_test(label, args.test_ratio)
Ytrain = Ytrain.tocsr()
Ytest  = Ytest.tocsr()

Nfeat  = X.shape[1]
Nprot  = Ytrain.shape[1]
Ncmpd  = Ytrain.shape[0]

batch_size = args.batch_size
h_size     = args.hsize
reg        = args.reg
zreg       = args.zreg
dropout    = args.dropout
res_reg    = 3e-3
lrate      = 0.001
lrate_decay = 0.1 #0.986
lrate_min  = 3e-5
epsilon    = 1e-5
model      = args.model

Ytest_std  = np.std( Ytest.data ) if Ytest.nnz > 0 else np.nan

print("Matrix:         %s" % args.y)
print("Side info:      %s" % args.side)
print("Test ratio:     %.2f" % args.test_ratio)
print("Num y train:    %d" % Ytrain.nnz)
print("Num y test:     %d" % Ytest.nnz)
print("Num compounds:  %d" % Ncmpd)
print("Num proteins:   %d" % Nprot)
print("Num features:   %d" % Nfeat)
print("St. deviation:  %f" % Ytest_std)
print("-----------------------")
print("Num epochs:     %d" % args.epochs)
print("Hidden size:    %d" % h_size)
print("reg:            %.1e" % reg)
print("Z-reg:          %.1e" % zreg)
print("Dropout:        %.2f" % dropout)
print("Learning rate:  %.1e" % lrate)
print("Batch size:     %d"   % batch_size)
print("Model:          %s"   % model)
print("-----------------------")

## variables for the model
W1 = tf.Variable(tf.random_uniform([Nfeat, h_size], minval=-1/np.sqrt(Nfeat), maxval=1/np.sqrt(Nfeat)))
b1 = tf.Variable(tf.random_uniform([h_size], minval=-1/np.sqrt(h_size), maxval=1/np.sqrt(h_size)))
W2 = tf.Variable(tf.random_uniform([Nprot, h_size], minval=-1/np.sqrt(h_size), maxval=1/np.sqrt(h_size)))
# b2 = tf.Variable(tf.constant(Ytrain.data.mean(), shape=[Nprot], dtype=tf.float32))
b2 = tf.Variable(tf.random_uniform([Nprot], minval=-1/np.sqrt(Nprot), maxval=1/np.sqrt(Nprot)))
b2g = tf.constant(Ytrain.data.mean(), dtype=tf.float32)
Z  = tf.Variable(tf.random_uniform([Ncmpd, h_size], minval=-1/np.sqrt(h_size), maxval=1/np.sqrt(h_size)))

## layers for residual layer
Wres1 = tf.Variable(tf.random_uniform([h_size, h_size], minval=-1/np.sqrt(h_size), maxval=1/np.sqrt(h_size)), name="Wres1")
bres1 = tf.Variable(tf.zeros(h_size), name="bres1")
Wres2 = tf.Variable(tf.random_uniform([h_size, h_size], minval=-1/np.sqrt(h_size), maxval=1/np.sqrt(h_size)), name="Wres2")
bres2 = tf.Variable(tf.zeros(h_size), name="bres2")

## inputs
y_val      = tf.placeholder(tf.float32)
y_idx_prot = tf.placeholder(tf.int64)
y_idx_comp = tf.placeholder(tf.int64)
z_idx      = tf.placeholder(tf.int64)
sp_indices = tf.placeholder(tf.int64)
sp_shape   = tf.placeholder(tf.int64)
sp_ids_val = tf.placeholder(tf.int64)
tr_ind     = tf.placeholder(tf.bool)

def l1_reg(tensor, weight=1.0, scope=None):
  with tf.op_scope([tensor], scope, 'L1Regularizer'):
    l1_weight = tf.convert_to_tensor(weight,
                                     dtype=tensor.dtype.base_dtype,
                                     name='weight')
    return tf.mul(l1_weight, tf.reduce_sum(tf.abs(tensor)), name='value')

def batch_norm_wrapper(inputs, is_training, decay = 0.999):
    scale = tf.Variable(tf.ones([inputs.get_shape()[-1]]))
    beta = tf.Variable(tf.zeros([inputs.get_shape()[-1]]))
    pop_mean = tf.Variable(tf.zeros([inputs.get_shape()[-1]]), trainable=False)
    pop_var = tf.Variable(tf.ones([inputs.get_shape()[-1]]), trainable=False)

    if is_training is not None:
        batch_mean, batch_var = tf.nn.moments(inputs,[0])
        train_mean = tf.assign(pop_mean,
                               pop_mean * decay + batch_mean * (1 - decay))
        train_var = tf.assign(pop_var,
                              pop_var * decay + batch_var * (1 - decay))
        with tf.control_dependencies([train_mean, train_var]):
            return tf.nn.batch_normalization(inputs,
                train_mean, train_var, beta, scale, epsilon)
    else:
        return tf.nn.batch_normalization(inputs,
            pop_mean, pop_var, beta, scale, epsilon)

## regularization parameter
lambda_reg = tf.placeholder(tf.float32)
lambda_zreg = tf.placeholder(tf.float32)
learning_rate = tf.placeholder(tf.float32)
dropout_keep  = tf.placeholder(tf.float32)

## model setup
sp_ids     = tf.SparseTensor(sp_indices, sp_ids_val, sp_shape)
# h1         = tf.nn.elu(tf.nn.embedding_lookup_sparse(W1, sp_ids, None, combiner = "sum") + b1)
# h1         = tf.nn.relu6(tf.nn.embedding_lookup_sparse(W1, sp_ids, None, combiner = "sum") + b1)
l1         = tf.nn.embedding_lookup_sparse(W1, sp_ids, None, combiner = "sum") + b1
Ze         = tf.nn.embedding_lookup(Z, z_idx)
if model == "linear":
    h1 = l1 + Ze
elif model == "non_linear_z":
    h1 = tf.tanh(l1 + Ze)
elif model == "main":
    h1 = tf.tanh(l1) + Ze
elif model == "residual":
    h1tmp = tf.tanh(l1 + Ze)
    #h1    = h1tmp + tf.tanh(tf.matmul(Wres2, tf.tanh(tf.matmul(Wres1, h1tmp) + bres1)) + bres2)
    h1 = h1tmp + tf.tanh(tf.matmul(h1tmp, Wres1) + bres1)
elif model == "residual2":
    h1tmp  = l1 + Ze
    #h1    = h1tmp + tf.tanh(tf.matmul(Wres2, tf.tanh(tf.matmul(Wres1, h1tmp) + bres1)) + bres2)
    h1tmp2 = tf.tanh(tf.matmul(h1tmp, Wres1) + bres1)
    h1     = tf.tanh(h1tmp + tf.matmul(h1tmp2, Wres2) + bres2)
elif model == "relu":
    h1 = tf.nn.relu(l1 + Ze)
else:
    raise ValueError("Parameter 'model' has unknown value (%s)." % model)

if dropout is None:
    dropout = 1.0

if dropout < 1.0:
    h1 = tf.nn.dropout(h1, keep_prob=dropout_keep)


## batch normalization doesn't work that well in comparison to Torch 
# h1         = batch_norm_wrapper(l1, tr_ind)

h1e        = tf.nn.embedding_lookup(h1, y_idx_comp)
W2e        = tf.nn.embedding_lookup(W2, y_idx_prot)
b2e        = tf.nn.embedding_lookup(b2, tf.squeeze(y_idx_prot, [1]))
l2         = tf.squeeze(tf.matmul(h1e, W2e, transpose_b=True), [1, 2]) + b2e
y_pred     = l2 + b2g

## batch normalization doesn't work that well in comparison to Torch 
# scale2e    = tf.nn.embedding_lookup(scale2, tf.squeeze(y_idx_prot, [1]))
# beta2e     = tf.nn.embedding_lookup(beta2, tf.squeeze(y_idx_prot, [1]))
# batch_mean2, batch_var2 = tf.nn.moments(l2,[0])
# z2         = (l2 - batch_mean2) / tf.sqrt(batch_var2 + epsilon)
# y_pred     = scale2e * l2 + b2g

b_ratio = np.float32(Ncmpd) / np.float32(batch_size)

y_loss     = tf.reduce_sum(tf.square(y_val - y_pred))
#l2_reg     = lambda_reg * tf.global_norm((W1, W2))**2 + lambda_zreg * b_ratio * tf.nn.l2_loss(Ze)
l2_reg     = lambda_reg * tf.global_norm((W1, W2))**2 + lambda_zreg * tf.nn.l2_loss(Z)
if model == "residual" or model == "residual2":
    l2_reg += res_reg * tf.nn.l2_loss(Wres1) + res_reg * tf.nn.l2_loss(Wres2)

loss       = l2_reg + y_loss / tf.to_float(tf.shape(z_idx)[0])

# Use the adam optimizer
train_op   = tf.train.AdamOptimizer(learning_rate).minimize(loss)

def select_rows(X, row_idx):
  Xtmp = X[row_idx]
  indices = np.zeros((Xtmp.nnz, 1), dtype = np.int64)
  for i in range(row_idx.shape[0]):
    indices[ Xtmp.indptr[i] : Xtmp.indptr[i+1], 0 ] = i
  shape = [row_idx.shape[0], X.shape[1]]
  return indices, shape, Xtmp.indices.astype(np.int64, copy=False)

def select_y(X, row_idx):
  Xtmp = X[row_idx]
  indices = np.zeros((Xtmp.nnz, 1), dtype = np.int64)
  for i in range(row_idx.shape[0]):
    indices[ Xtmp.indptr[i] : Xtmp.indptr[i+1], 0 ] = i
  shape = [row_idx.shape[0], X.shape[1]]
  return indices, shape, Xtmp.indices.astype(np.int64, copy=False).reshape(-1, 1), Xtmp.data.astype(np.float32, copy=False)

Xi, Xs, Xv = select_rows(X, np.arange(X.shape[0]))
Yte_idx_comp, Yte_shape, Yte_idx_prot, Yte_val = select_y(Ytest, np.arange(Ytest.shape[0]))
Ytr_idx_comp, Ytr_shape, Ytr_idx_prot, Ytr_val = select_y(Ytrain, np.arange(Ytrain.shape[0]))

with tf.Session() as sess:
  sess.run(tf.global_variables_initializer())
  best_train_sse = np.inf
  decay_cnt = 0

  for epoch in range(args.epochs):
    rIdx = np.random.permutation(Ytrain.shape[0])
    
    if decay_cnt > 2:
      lrate = np.max( [lrate * lrate_decay, lrate_min] )
      decay_cnt = 0
      best_train_sse = train_sse
      if lrate <= 1e-6:
          print("Converged, stopping at learning rate of 1e-6.")
          break

    ## mini-batch loop
    for start in np.arange(0, Ytrain.shape[0], batch_size):
      idx = rIdx[start : min(Ytrain.shape[0], start + batch_size)]
      bx_indices, bx_shape, bx_ids_val           = select_rows(X, idx)
      by_idx_comp, by_shape, by_idx_prot, by_val = select_y(Ytrain, idx)

      sess.run(train_op, feed_dict={sp_indices: bx_indices,
                                    sp_shape:   bx_shape,
                                    sp_ids_val: bx_ids_val,
                                    z_idx:      idx,
                                    y_idx_comp: by_idx_comp,
                                    y_idx_prot: by_idx_prot,
                                    y_val:      by_val,
                                    tr_ind:     True,
                                    lambda_reg:  reg,
                                    lambda_zreg: zreg,
                                    learning_rate: lrate,
                                    dropout_keep: dropout})


    ## epoch's Ytest error
    if epoch % 1 == 0:
      test_sse = sess.run(y_loss,  feed_dict = {sp_indices: Xi,
                                                 sp_shape:   Xs,
                                                 sp_ids_val: Xv,
                                                 z_idx:      np.arange(0, Ytest.shape[0]),
                                                 y_idx_comp: Yte_idx_comp,
                                                 y_idx_prot: Yte_idx_prot,
                                                 y_val:      Yte_val,
                                                 tr_ind:     False,
                                                 dropout_keep: 1.0})
      train_sse = sess.run(y_loss, feed_dict = {sp_indices: Xi,
                                                 sp_shape:   Xs,
                                                 sp_ids_val: Xv,
                                                 z_idx:      np.arange(0, Ytrain.shape[0]),
                                                 y_idx_comp: Ytr_idx_comp,
                                                 y_idx_prot: Ytr_idx_prot,
                                                 y_val:      Ytr_val,
                                                 tr_ind:     False,
                                                 dropout_keep: 1.0})
      if train_sse <= best_train_sse:
        best_train_sse = train_sse
      else:
        decay_cnt += 1

      W1_l2 = sess.run(tf.nn.l2_loss(W1))
      W2_l2 = sess.run(tf.nn.l2_loss(W2))
      Z_l2  = sess.run(tf.nn.l2_loss(Z))
      test_rmse = np.sqrt( test_sse / Yte_val.shape[0]) if Yte_val.shape[0] > 0 else np.nan
      train_rmse = np.sqrt( train_sse / Ytr_val.shape[0])

      print("%3d. RMSE(test) = %.5f   RMSE(train) = %.5f   ||W1|| = %.2f   ||W2|| = %.2f ||Z|| = %.2f  lr = %.0e" % (epoch, test_rmse, train_rmse, np.sqrt(W1_l2), np.sqrt(W2_l2), np.sqrt(Z_l2), lrate) )
  ## after the training loop
  if args.save is not None:
    saver = tf.train.Saver()
    saver.save(sess, args.save)
    print("Saved model to '%s'." % args.save)

