
import copy
import numpy as np
np.set_printoptions(suppress=True, precision=8)
import os
import sys
from sklearn.metrics import classification_report
import tensorflow as tf

path = os.path.join(os.path.dirname(__file__), os.pardir)
sys.path.append(os.path.abspath(path))

import dataset
import dataset_loaders
import neural_networks.feed_forward_neural_network as ffnn
import neural_networks.recurrent_neural_network as rnn
import neural_networks.utils
import priority_dataset

FLAGS = tf.app.flags.FLAGS

# training constants
tf.app.flags.DEFINE_integer('batch_size', 
                            32,
                            """Number of samples in a batch.""")
tf.app.flags.DEFINE_integer('num_epochs', 
                            100,
                            """Number of training epochs.""")
tf.app.flags.DEFINE_string('snapshot_dir', 
                           '../../data/snapshots/test/',
                           """Path to directory where to save weights.""")
tf.app.flags.DEFINE_string('summary_dir', 
                           '../../data/summaries/test',
                           """Path to directory where to save summaries.""")
tf.app.flags.DEFINE_string('julia_weights_filepath', 
                           '../../data/networks/test.weights',
                           """Path to file where to save julia weights.""")
tf.app.flags.DEFINE_integer('save_every', 
                            1000000,
                            """Number of epochs between network saves.""")
tf.app.flags.DEFINE_bool('verbose', 
                            True,
                            """Wether or not to print out progress.""")
tf.app.flags.DEFINE_integer('debug_size', 
                            None,
                            """Debug size to use.""")
tf.app.flags.DEFINE_integer('random_seed', 
                            1,
                            """Random seed value to use.""")
tf.app.flags.DEFINE_bool('load_network', 
                            False,
                            """Wether or not to load from a saved network.""")
tf.app.flags.DEFINE_integer('log_summaries_every', 
                            2,
                            """Number of batches between logging summaries.""")
tf.app.flags.DEFINE_integer('save_weights_every', 
                            1,
                            """Number of batches between logging summaries.""")
tf.app.flags.DEFINE_bool('balanced_class_loss', 
                            False,
                            """Whether or not to balance the classes in 
                            classification loss by reweighting.""")
tf.app.flags.DEFINE_integer('target_index', 
                            None,
                            """Target index to fit exclusively if set (zero-based).
                            This must be accompanied by setting output_dim to 1.""")

# network constants
tf.app.flags.DEFINE_integer('max_norm', 
                            100000,
                            """Maximum gradient norm.""")
tf.app.flags.DEFINE_integer('hidden_dim', 
                            64,
                            """Hidden units in each hidden layer.""")
tf.app.flags.DEFINE_integer('num_hidden_layers', 
                            2,
                            """Number of hidden layers.""")
tf.app.flags.DEFINE_string('hidden_layer_dims', 
                            '',
                            """Hidden layer sizes, empty list means use hidden_dim.""")
tf.app.flags.DEFINE_float('learning_rate', 
                            0.0005,
                            """Initial learning rate to use.""")
tf.app.flags.DEFINE_float('decrease_lr_threshold', 
                            .001,
                            """Percent decrease in validation loss below 
                            which the learning rate will be decayed.""")
tf.app.flags.DEFINE_float('decay_lr_ratio', 
                            .95,
                            """Learning rate decay factor.""")
tf.app.flags.DEFINE_float('min_lr', 
                            .000005,
                            """Minimum learning rate value.""")
tf.app.flags.DEFINE_string('loss_type', 
                           'ce',
                           """Type of loss to use {mse, ce}.""")
tf.app.flags.DEFINE_string('task_type', 
                           'regression',
                           """Type of task {regression, classification}.""")
tf.app.flags.DEFINE_integer('num_target_bins', 
                            None,
                            """Number of bins into which to discretize targets.""")
tf.app.flags.DEFINE_float('dropout_keep_prob', 
                            1.,
                            """Probability to keep a unit in dropout.""")
tf.app.flags.DEFINE_boolean('use_batch_norm', 
                            False,
                            """Whether to use batch norm (True removes dropout).""")
tf.app.flags.DEFINE_float('l2_reg', 
                            0.0,
                            """Probability to keep a unit in dropout.""")
tf.app.flags.DEFINE_float('eps', 
                            1e-8,
                            """Minimum probability value.""")

# dataset constants
tf.app.flags.DEFINE_string('dataset_filepath',
                            '../../data/datasets/risk.jld',
                            'Filepath of dataset.')
tf.app.flags.DEFINE_integer('input_dim', 
                            166,
                            """Dimension of input.""")
tf.app.flags.DEFINE_integer('timesteps', 
                            1,
                            """Number of input timesteps.""")
tf.app.flags.DEFINE_integer('output_dim', 
                            5,
                            """Dimension of output.""")
tf.app.flags.DEFINE_bool('use_priority', 
                            False,
                            """Wether or not to use a prioritized dataset.""")
tf.app.flags.DEFINE_float('priority_alpha', 
                            0.25,
                            """Alpha parameter for prioritization.""")
tf.app.flags.DEFINE_float('priority_beta', 
                            1.0,
                            """Beta parameter for prioritization.""")

# bootstrapping constants
tf.app.flags.DEFINE_integer('bootstrap_iterations', 
                            10,
                            """Number of iterations of collecting a bootstrapped dataset and fitting it.""")
tf.app.flags.DEFINE_integer('num_proc', 
                            1,
                            """Number of processes to use for dataset collection.""")
tf.app.flags.DEFINE_integer('num_scenarios', 
                            1,
                            """Number of scenarios in each dataset.""")
tf.app.flags.DEFINE_string('initial_network_filepath',
                            'none',
                            'Filepath of initial network or none.')

def custom_parse_flags(flags):
    if flags.hidden_layer_dims != '':
        dims = flags.hidden_layer_dims.split(' ')
        dims = [int(dim) for dim in dims]
    else:
        dims = [flags.hidden_dim for _ in range(flags.num_hidden_layers)]

    flags.hidden_layer_dims = dims
    print('Building network with hidden dimensions: {}'.format(
            flags.hidden_layer_dims))

def report_poorly_performing_indices_features(idxs, data, unnorm_data):
    batch_idxs = data['batch_idxs']
    seeds = data['seeds']
    for idx in idxs:
        for i, b in enumerate(batch_idxs):
            if b > idx:
                break
        seed = seeds[i]
        if i > 0:
            veh_idx = idx - batch_idxs[i - 1] + 1
        else:
            veh_idx = idx + 1
        print('seed: {}\tveh idx: {}'.format(seed, veh_idx))
        print('features: {}'.format(unnorm_data['x_train'][idx,:5]))
        print('targets: {}'.format(unnorm_data['y_train'][idx]))
        print('seed num veh: {}'.format(batch_idxs[i] - batch_idxs[i-1]))

def classification_score(y, y_pred, name, y_null=None):
    print('\nclassification results for {}'.format(name))
    for tidx in range(y.shape[1]):
        print('target: {}'.format(tidx))
        print(classification_report(y[:,tidx], y_pred[:,tidx]))
        input()

def regression_score(y, y_pred, name, data=None, unnorm_data=None, eps=1e-16, 
        y_null=None):
    # prevent overflow during the sum of the log terms
    y_pred = y_pred.astype(np.float128)
    # also threshold values to prevent log exception (throws off loss value)
    y_pred[y_pred < eps] = eps
    y_pred[y_pred > 1 - eps] = 1 - eps
    
    np.sum(y * np.log(y_pred))
    np.sum((1 - y) * np.log(1 - y_pred)) 
    ll = np.sum(y * np.log(y_pred)) + np.sum((1 - y) * np.log(1 - y_pred)) 
    ce = -ll
    mse = np.sum((y - y_pred) ** 2)
    r2 = 1 - ((y - y_pred) ** 2).sum() / ((y - y.mean(axis=0)) ** 2).sum()

    # worst indices
    if len(np.shape(y)) > 1:
        max_mse_idx = np.argmax(np.sum((y - y_pred) ** 2, axis=1))
        max_ce_idx = np.argmax(np.sum(-(y * np.log(y_pred) + (1 - y) * np.log(1 - y_pred)), axis=1))
    else:
        max_mse_idx = np.argmax((y - y_pred) ** 2)
        max_ce_idx = np.argmax(-(y * np.log(y_pred) + (1 - y) * np.log(1 - y_pred)))

    num_samples = len(y_pred)
    print("\n{} final cross entropy: {}".format(name, ce / num_samples))
    print("{} final mse: {}".format(name, mse / num_samples))
    print("{} final r2: {}".format(name, r2))
    print("{} worst ce (idx {}):\n y: {} y_pred: {}".format(
        name, max_ce_idx, y[max_ce_idx], y_pred[max_ce_idx]))
    print("{} worst mse (idx {}):\n y: {} y_pred: {}".format(
        name, max_mse_idx, y[max_mse_idx], y_pred[max_mse_idx]))

    # convert to julia format the worst indices
    if data is not None:
        num_report = 5
        print('\noverall poorly predicted')
        idxs = np.argsort(np.sum(-(y * np.log(y_pred) + (1 - y) * np.log(1 - y_pred)), axis=1))[-num_report:]
        report_poorly_performing_indices_features(idxs, data, unnorm_data)
        print('\nrear end collisions poorly predicted')
        idxs = np.argsort(np.sum(-(y[:,1:3] * np.log(y_pred[:,1:3]) + (1 - y[:,1:3]) * np.log(1 - y_pred[:,1:3])), axis=1))[-num_report:]
        report_poorly_performing_indices_features(idxs, data, unnorm_data)
        print('\nhard brakes poorly predicted')
        idxs = np.argsort(-(y[:,3] * np.log(y_pred[:,3]) + (1 - y[:,3]) * np.log(1 - y_pred[:,3])))[-num_report:]
        report_poorly_performing_indices_features(idxs, data, unnorm_data)

    # psuedo r^2 and other metrics
    if y_null is not None:
        if len(np.shape(y_null)) > 0:
            y_null[y_null < eps] = eps
            y_null[y_null > 1 - eps] = 1 - eps

        null_ll = np.sum(y * np.log(y_null)) + -np.sum((1 - y) * np.log(1 - y_null))
        mcfadden_r2 = 1 - ll / null_ll
        tjur_r2 = np.mean(y_pred[y>=.5], axis=0) - np.mean(y_pred[y<.5], axis=0)
        y_class = np.zeros(y.shape)
        y_class[y>.5] = 1
        y_class = y_class.flatten()
        y_pred_class = (copy.deepcopy(y_pred) + .5).astype(int)
        y_pred_class = y_pred_class.flatten()
        acc = len(np.where(y_class == y_pred_class)[0]) / np.prod(y_class.shape)
        prec_idxs = np.where(y_pred_class == 1)[0]
        prec = recall = len(np.where(y_class[prec_idxs] == 1)[0])
        if len(prec_idxs) > 0:
            prec /= len(prec_idxs)
            recall /= len(np.where(y_class == 1)[0])
        else:
            prec = 0
            recall = 0

        print("mcfadden r^2: {}\tll: {}\tnull ll: {}".format(mcfadden_r2, ll, null_ll))
        print("tjur_r2: {}".format(tjur_r2))
        print("acc: {}\tprecision: {}\trecall: {}".format(acc, prec, recall))
        
    return ce, mse, r2

def main(argv=None):
    # custom parse of flags for list input
    custom_parse_flags(FLAGS)

    # set random seeds
    np.random.seed(FLAGS.random_seed)
    tf.set_random_seed(FLAGS.random_seed)

    # load dataset
    input_filepath = FLAGS.dataset_filepath
    data = dataset_loaders.risk_dataset_loader(
        input_filepath, shuffle=True, train_split=.9, 
        debug_size=FLAGS.debug_size, timesteps=FLAGS.timesteps,
        num_target_bins=FLAGS.num_target_bins, balanced_class_loss=FLAGS.balanced_class_loss, target_index=FLAGS.target_index)

    if FLAGS.use_priority:
        d = priority_dataset.PrioritizedDataset(data, FLAGS)
    else:
        if FLAGS.balanced_class_loss:
            d = dataset.WeightedDataset(data, FLAGS)
        else:
            d = dataset.Dataset(data, FLAGS)

    print(np.mean(d.data['y_train'], axis=0))
    print(np.mean(d.data['y_val'], axis=0))
    y = copy.deepcopy(d.data['y_val'])
    y[y==0.] = 1e-8
    y[y==1.] = 1 - 1e-8
    baseline = np.mean(y, axis=0)
    ce = -np.sum(y * np.log(baseline)) + -np.sum((1 - y) * np.log(1 - baseline))
    mse = np.sum((y - baseline) ** 2)
    r2 = 1 - ((y - baseline) ** 2).sum() / ((y - y.mean(axis=0)) ** 2).sum()
    num_samples = len(y)
    print("cross entropy from outputting validation mean: {}".format(ce / num_samples))
    print("mse from outputting validation mean: {}".format(mse / num_samples))
    print("r2 from outputting validation mean: {}".format(r2))
    
    ce = -np.sum(y * np.log(y)) + -np.sum((1 - y) * np.log(1 - y))
    print("cross entropy from outputting correct values: {}".format(ce / num_samples))
    try:
        ce = -np.sum(y[:,3] * np.log(y[:,3])) + -np.sum((1 - y[:,3]) * np.log(1 - y[:,3]))
        print("hard brake cross entropy from outputting correct values: {}".format(ce / num_samples))
    except:
        pass
    # fit the model
    with tf.Session(config=tf.ConfigProto(log_device_placement=False)) as session:
        # if the timestep dimension is > 1, use recurrent network
        if FLAGS.timesteps > 1:
            network = rnn.RecurrentNeuralNetwork(session, FLAGS)
        else:
            if FLAGS.task_type == 'classification':
                if FLAGS.balanced_class_loss:
                    network = ffnn.WeightedClassificationFeedForwardNeuralNetwork(session, FLAGS)
                else:
                    network = ffnn.ClassificationFeedForwardNeuralNetwork(session, FLAGS)
            else:
                network = ffnn.FeedForwardNeuralNetwork(session, FLAGS)
        network.fit(d)

        # save weights to a julia-compatible weight file
        neural_networks.utils.save_trainable_variables(
            FLAGS.julia_weights_filepath, session, data)


        y_idxs = np.where(np.sum(data['y_val'][:10000], axis=1) > 1e-4)[0]
        y_idxs = np.random.permutation(y_idxs)[:10]
        y_pred = network.predict(data['x_val'][y_idxs])

        for y_pred_s, y_s in zip(y_pred, data['y_val'][y_idxs]):
            print(y_pred_s)
            print(y_s)
            print()

        # determine the function used for assessment based on the task
        score = (regression_score if FLAGS.task_type == 'regression' else classification_score)

        # final train loss
        y_pred = network.predict(data['x_train'])
        y = data['y_train']
        y_null = np.mean(y, axis=0)
        score(y, y_pred, 'train', y_null=y_null)

        # final validation loss
        y_pred = network.predict(data['x_val'])
        y = data['y_val']
        y_null = np.mean(y, axis=0)
        score(y, y_pred, 'val', y_null=y_null)

        # only makes sense to run this with regression
        if FLAGS.task_type == 'regression':
            # final validation loss, hard braking
            y_pred = network.predict(data['x_val'])
            y_pred = y_pred[:, 3]
            y = data['y_val'][:, 3]
            y_null = np.mean(y, axis=0)
            score(y, y_pred, 'hard brake', y_null=y_null)
        
        # score again the unshuffled data
        if FLAGS.task_type == 'regression':
            data = dataset_loaders.risk_dataset_loader(
                input_filepath, shuffle=False, train_split=1., 
                debug_size=FLAGS.debug_size)
            unnorm_data = dataset_loaders.risk_dataset_loader(
                input_filepath, shuffle=False, train_split=1., 
                debug_size=FLAGS.debug_size, normalize=False)

            y_pred = network.predict(data['x_train'])
            y = data['y_train']
            score(y, y_pred, 'unshuffled', data, unnorm_data)
          
        # save weights to a julia-compatible weight file
        neural_networks.utils.save_trainable_variables(
            FLAGS.julia_weights_filepath, session, data)

if __name__ == '__main__':
    tf.app.run()

"""
Notes:
1. Cross entropy loss with logistic output
    a. using cross entropy with logistic output seems to make extreme overfitting difficult. I think the reason for this is that  
"""
