import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--mat",   type=str,   help="mat file with observations X and side info", required=True)
parser.add_argument("--epochs", type=int,  help="number of epochs", default = 2000)
parser.add_argument("--hsize", type=int,   help="size of the hidden layer", default = 30)
parser.add_argument("--batch-size", type=int,   help="batch size", default = 512)
args = parser.parse_args()

import tensorflow as tf
import scipy.io
import numpy as np
import chemblnet as cn
import chemblnet.vbutils as vb

data  = scipy.io.matlab.loadmat(args.mat)
label = data["X"]
Fu    = data["Fu"].todense()
Fv    = data["Fv"].todense()
# 109, 167, 168, 204, 214, 215

Ytrain, Ytest = cn.make_train_test(label, 0.5)

Ytrain = Ytrain.tocsr()
Ytest  = Ytest.tocsr()

# learning parameters
Y_prec      = 1.5
h1_size     = args.hsize

batch_size  = args.batch_size
lrate       = 1e-1
lrate_decay = 1.0

print("Data file:      %s" % args.mat)
print("Y size:         [%d, %d]" % (label.shape[0], label.shape[1]))
print("Num row feat:   %d" % Fu.shape[1])
print("Num col feat:   %d" % Fv.shape[1])
print("Test stdev:     %.4f" % np.std( Ytest.data ))
print("-----------------------")
print("Num epochs:     %d" % args.epochs)
print("Hidden size:    %d" % args.hsize)
print("Learning rate:  %.1e" % lrate)
print("Batch size:     %d"   % batch_size)
print("-----------------------")


extra_info  = False

## y_val is a vector of values and y_coord gives their coordinates
y_val   = tf.placeholder(tf.float32, name="y_val")
y_coord = tf.placeholder(tf.int32, shape=[None, 2], name="y_coord")
#y_idx_u = tf.placeholder(tf.int64)
#y_idx_v = tf.placeholder(tf.int64)
x_u     = tf.placeholder(tf.float32, shape=[None, Fu.shape[1]], name="x_u")
x_v     = tf.placeholder(tf.float32, shape=[None, Fv.shape[1]], name="x_v")
u_idx   = tf.placeholder(tf.int64, name="u_idx")
#v_idx   = tf.placeholder(tf.int64, name="v_idx")

learning_rate = tf.placeholder(tf.float32, name = "learning_rate")

## ratio of total training points to mini-batch training points, for the current batch
tb_ratio = tf.placeholder(tf.float32, name = "tb_ratio")
bsize    = tf.placeholder(tf.float32, name = "bsize")

## model
beta_u = vb.NormalGammaUni("beta_u", shape = [Fu.shape[1], h1_size], initial_stdev = 0.1, fixed_prec = False)
beta_v = vb.NormalGammaUni("beta_v", shape = [Fv.shape[1], h1_size], initial_stdev = 0.1, fixed_prec = False)
U      = vb.NormalGammaUni("U",    shape = [Ytrain.shape[0], h1_size], initial_stdev = 1.0, fixed_prec = False)
V      = vb.NormalGammaUni("V",    shape = [Ytrain.shape[1], h1_size], initial_stdev = 1.0, fixed_prec = False)

global_mean = tf.constant(Ytrain.data.mean(), dtype=tf.float32)

## means
Umean_b = tf.gather(U.mean, u_idx)
Vmean_b = V.mean
h_u     = tf.matmul(x_u, beta_u.mean) + Umean_b
h_v     = tf.matmul(x_v, beta_v.mean) + Vmean_b
y_pred  = tf.matmul(h_u, h_v, transpose_b=True)
y_pred_b = global_mean + tf.gather_nd(y_pred, y_coord)

y_sse   = tf.reduce_sum( tf.square(y_val - y_pred_b) )
y_loss  = Y_prec / 2.0 * y_sse

## variance
Uvar_b  = tf.exp(tf.gather(U.logvar, u_idx))
Vvar_b  = V.var
h_u_var = tf.matmul(tf.square(x_u), beta_u.var) + Uvar_b
h_v_var = tf.matmul(tf.square(x_v), beta_v.var) + Vvar_b

y_var    = Y_prec / 2.0 * tf.matmul(h_u_var, h_v_var + tf.square(h_v), transpose_b=True) + Y_prec / 2.0 * tf.matmul(tf.square(h_u), h_v_var, transpose_b=True)
var_loss = tf.gather_nd(y_var, y_coord)

#E_usq   = tf.add(h1var_b, tf.square(h1_b))
#y_var1  = Y_prec / 2.0 * tf.reduce_sum(tf.squeeze(tf.batch_matmul(E_usq, Vvar_b, adj_y=True), [1, 2]))
#y_var2  = Y_prec / 2.0 * tf.reduce_sum(tf.squeeze(tf.batch_matmul(h1var_b, tf.square(Vmean_b), adj_y=True), [1, 2]))

L_D     = tb_ratio * (y_loss + var_loss)
L_prior = beta_u.prec_div() + beta_v.prec_div() + U.prec_div() + V.prec_div() + beta_u.normal_div() + beta_v.normal_div() + U.normal_div_partial(Umean_b, Uvar_b, bsize) + V.normal_div()
loss    = L_D + L_prior

train_op = tf.train.AdagradOptimizer(learning_rate).minimize(loss)
#train_op = tf.train.AdamOptimizer(learning_rate).minimize(loss)
#train_op = tf.train.MomentumOptimizer(1e-7, 0.90).minimize(loss)

######################################################

def select_y(X, row_idx):
  Xtmp = X[row_idx]
  return np.column_stack(Xtmp.nonzero()), Xtmp.data.astype(np.float32), [0, 0]

rIdx = np.random.permutation(Ytrain.shape[0])

# ---------- test data ------------- #
Yte_coord, Yte_values, Yte_shape = select_y(Ytest, np.arange(Ytest.shape[0]))

# ------- train data (all) --------- #
Ytr_coord, Ytr_values, Ytr_shape =  select_y(Ytrain, np.arange(Ytrain.shape[0]))

#with tf.Session() as sess:
best_train_rmse = np.inf
decay_count = 0

sess = tf.Session()
if True:
  sess.run(tf.global_variables_initializer())

  for epoch in range(args.epochs):
    rIdx = np.random.permutation(Ytrain.shape[0])

    ## mini-batch loop
    for start in np.arange(0, Ytrain.shape[0], batch_size):
      if start + batch_size > Ytrain.shape[0]:
        break
      idx = rIdx[start : start + batch_size]
      by_coord, by_values, by_shape = select_y(Ytrain, idx)

      sess.run(train_op, feed_dict={x_u:     Fu[idx,:],
                                    x_v:     Fv,
                                    y_coord: by_coord,
                                    y_val:   by_values,
                                    u_idx:   idx,
                                    tb_ratio:       Ytrain.nnz / float(by_values.shape[0]),
                                    learning_rate:  lrate,
                                    bsize:          batch_size
                                    })
    ## TODO: check from here

    ## epoch's Ytest error
    if epoch % 1 == 0:
      test_y_pred = sess.run(y_pred_b,
                             feed_dict = {x_u:  Fu,
                                          x_v:  Fv,
                                          y_coord:  Yte_coord,
                                          y_val:    Yte_values,
                                          u_idx:    np.arange(Ytrain.shape[0])})
      test_rmse = np.sqrt(np.mean(np.square(test_y_pred - Yte_values)))

#      Ltr = sess.run([L_D, loss, beta.prec_div(), beta.normal_div()],
#                     feed_dict={x_indices:  Xi,
#                               x_shape:    Xs,
#                               x_ids_val:  Xv,
#                               x_idx_comp: Xindices,
#                               y_idx_comp: Ytr_idx_comp,
#                               y_idx_prot: Ytr_idx_prot,
#                               y_val:      Ytr_val,
#                               tb_ratio:   1.0,
#                               bsize:      Ytrain.shape[0]
#                               })
#      beta_l2      = np.sqrt(sess.run(tf.nn.l2_loss(beta.mean)))
#      beta_std_min = np.sqrt(sess.run(tf.reduce_min(beta.var)))
#      beta_prec    = sess.run(beta.prec)
#      V_prec       = sess.run(V.prec)
#      V_l2         = np.sqrt(sess.run(tf.nn.l2_loss(V.mean)))
#      Z_prec       = sess.run(Z.prec)
#      #W2_l2 = sess.run(tf.nn.l2_loss(W2))
#      test_rmse  = np.sqrt( test_sse  / Yte_val.shape[0])
#      train_rmse = np.sqrt( train_sse / Ytr_val.shape[0])

      if epoch % 20 == 0:
          print("Epoch\tRMSE(te, tr)\t  L_D,loss(train)\tbeta divergence\t\tmin(beta.std)\tbeta.prec\tl2(V.mu)")

      print("%3d.\t%.5f" % (epoch, test_rmse))
      if extra_info:
          #print("beta: [%s]" % beta.summarize(sess))
          #print("Z:    [%s]" % Z.summarize(sess))
          print("V:    [%s]" % V.summarize(sess))


