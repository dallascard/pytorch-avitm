import numpy as np
import tensorflow as tf
import itertools,time
import sys, os
from collections import OrderedDict
from copy import deepcopy
from time import time
import matplotlib.pyplot as plt
import pickle

slim = tf.contrib.slim

tf.reset_default_graph()


def xavier_init(fan_in, fan_out, constant=1):
    low = -constant*np.sqrt(6.0/(fan_in + fan_out))
    high = constant*np.sqrt(6.0/(fan_in + fan_out))
    return tf.random_uniform((fan_in, fan_out),
                             minval=low, maxval=high,
                             dtype=tf.float32)


class VAE(object):
    """
    See "Auto-Encoding Variational Bayes" by Kingma and Welling for more details.
    """
    def __init__(self, network_architecture, transfer_fct=tf.nn.softplus,
                 learning_rate=0.001, batch_size=100, init_bg=None, init_l2_strength=0.001):
        self.network_architecture = network_architecture
        self.transfer_fct = transfer_fct
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        #self.l2_strength = init_l2_strength
        #self.l2_strength = tf.placeholder(tf.float32, name="l2_strength")
        self.l2_strength = tf.placeholder(tf.float32, [network_architecture["n_z"], network_architecture["n_hidden_gener_1"]], name="l2_strength")
        print 'Learning Rate:', self.learning_rate

        # tf Graph input
        self.x = tf.placeholder(tf.float32, [None, network_architecture["n_input"]], name='input')
        self.keep_prob = tf.placeholder(tf.float32, name='keep_prob')

        self.h_dim = (network_architecture["n_z"]) # had a float before
        self.a = 1*np.ones((1 , self.h_dim)).astype(np.float32)                         # a    = 1
        self.prior_mean = tf.constant((np.log(self.a).T-np.mean(np.log(self.a),1)).T)          # prior_mean  = 0
        self.prior_var = tf.constant(  ( ( (1.0/self.a)*( 1 - (2.0/self.h_dim) ) ).T +       # prior_var = 0.99 + 0.005 = 0.995
                                ( 1.0/(self.h_dim*self.h_dim) )*np.sum(1.0/self.a,1) ).T  )
        self.prior_logvar = tf.log(self.prior_var)

        self._create_network(init_bg)
        with tf.name_scope('cost'):
            self._create_loss_optimizer()

        init = tf.initialize_all_variables()

        self.sess = tf.InteractiveSession()
        self.sess.run(init)

    def _create_network(self, init_bg=None):
        """
        steps:
        1. initialize weights
        2. build recognition network
        3. build reconstruction network
        """
        n_z = self.network_architecture['n_z']
        n_hidden_gener_1 = self.network_architecture['n_hidden_gener_1']

        self.network_weights = self._initialize_weights(n_hidden_gener_1, n_z, init_bg)

        en1 = slim.layers.linear(self.x, self.network_architecture['n_hidden_recog_1'], scope='FC_en1')
        en1 = tf.nn.softplus(en1, name='softplus1')
        en2 = slim.layers.linear(en1,    self.network_architecture['n_hidden_recog_2'], scope='FC_en2')
        en2 = tf.nn.softplus(en2, name='softplus2')
        en2_do = slim.layers.dropout(en2, self.keep_prob, scope='en2_dropped')
        self.posterior_mean   = slim.layers.linear(en2_do, self.network_architecture['n_z'], scope='FC_mean')
        self.posterior_logvar = slim.layers.linear(en2_do, self.network_architecture['n_z'], scope='FC_logvar')
        self.posterior_mean   = slim.layers.batch_norm(self.posterior_mean, scope='BN_mean')
        self.posterior_logvar = slim.layers.batch_norm(self.posterior_logvar, scope='BN_logvar')

        with tf.name_scope('z_scope'):
            eps = tf.random_normal((self.batch_size, n_z), 0, 1,                            # take noise
                                   dtype=tf.float32)
            self.z = tf.add(self.posterior_mean,
                            tf.multiply(tf.sqrt(tf.exp(self.posterior_logvar)), eps))         # reparameterization z
            self.posterior_var = tf.exp(self.posterior_logvar) 

        # DEBUG
        #p = slim.layers.softmax(self.z)
        p = self.z


        p_do = slim.layers.dropout(p, self.keep_prob, scope='p_dropped')               # dropout(softmax(z))
        #decoded = slim.layers.linear(p_do, n_hidden_gener_1, scope='FC_decoder', weights_regularizer=slim.l1_regularizer(0.01))
        #decoded = slim.layers.linear(p_do, n_hidden_gener_1, scope='FC_decoder')


        #decoded = tf.add(tf.matmul(p_do, self.network_weights['beta']), self.network_weights['background'])
        decoded = tf.add(tf.matmul(p_do, tf.exp(self.network_weights['beta'])), self.network_weights['background'])

        # DEBUG
        self.x_reconstr_mean = tf.nn.softmax(slim.layers.batch_norm(decoded, scope='BN_decoder'))                    # softmax(bn(50->1995))
        #self.x_reconstr_mean = tf.nn.softmax(decoded)                    # softmax(bn(50->1995))

        print self.x_reconstr_mean

    def _initialize_weights(self, n_hidden_gener_1, n_z, bg_init=None):
        all_weights = dict()

        all_weights['beta'] = tf.Variable(xavier_init(n_z, n_hidden_gener_1))

        if bg_init is not None:
            all_weights['background'] = tf.Variable(bg_init)
        else:
            all_weights['background'] = tf.Variable(tf.zeros(n_hidden_gener_1, dtype=tf.float32))

        return all_weights

    def _create_loss_optimizer(self):

        #self.x_reconstr_mean+=1e-10                                                     # prevent log(0)

        NL = -tf.reduce_sum(self.x * tf.log(self.x_reconstr_mean+1e-10), 1)     # cross entropy on categorical
        #reconstr_loss = -tf.reduce_sum(self.x * tf.log(self.x_reconstr_mean), 1)

        var_division    = self.posterior_var  / self.prior_var
        diff            = self.posterior_mean - self.prior_mean
        diff_term       = diff * diff / self.prior_var
        logvar_division = self.prior_logvar - self.posterior_logvar


        # DEBUG
        #KLD = 0.5 * (tf.reduce_sum(var_division + diff_term + logvar_division, 1) - self.h_dim )
        KLD = -0.5 * tf.reduce_sum(1 + self.posterior_logvar - tf.square(self.posterior_mean) - tf.exp(self.posterior_logvar))

        #l1_regularizer = tf.contrib.layers.l1_regularizer(
        #    scale=0.0001, scope=None
        #)
        #regularization_penalty = tf.contrib.layers.apply_regularization(l1_regularizer, [self.network_weights['beta']])
        #self.cost = tf.reduce_mean(NL + KLD + regularization_penalty)

        # SAGE-style regularization
        #self.cost = tf.add(tf.reduce_mean(NL + KLD), tf.reduce_sum(tf.multiply(self.l2_strength, tf.square(self.network_weights['beta']))))

        self.cost = tf.reduce_mean(NL + KLD)

        self.optimizer = \
            tf.train.AdamOptimizer(learning_rate=self.learning_rate,beta1=0.99).minimize(self.cost)

    def partial_fit(self, X, l2_strength):

        #if hasattr(self, 'decoder_weight'):
            #decoder_weight = self.decoder_weight
        #else:
        #decoder_weight = [v for v in tf.global_variables() if v.name=='FC_decoder/weights:0'][0]
        decoder_weight = self.network_weights['beta']
        background = self.network_weights['background']

        opt, cost, emb, bg = self.sess.run((self.optimizer, self.cost, decoder_weight, background), feed_dict={self.x: X, self.keep_prob: .8, self.l2_strength: l2_strength})
        return cost, emb, bg

    def test(self, X):
        """Test the model and return the lowerbound on the log-likelihood.
        """
        l2_strength = np.zeros(self.network_weights['beta'].shape)
        cost = self.sess.run((self.cost),feed_dict={self.x: np.expand_dims(X, axis=0),self.keep_prob: 1.0, self.l2_strength: l2_strength})
        return cost

    def topic_prop(self, X):
        """heta_ is the topic proportion vector. Apply softmax transformation to it before use.
        """
        l2_strength = np.zeros(self.network_weights['beta'].shape)
        theta_ = self.sess.run((self.z),feed_dict={self.x: np.expand_dims(X, axis=0),self.keep_prob: 1.0, self.l2_strength: l2_strength})
        return theta_

    def get_weights(self):
        return self.network_weights['beta'].values

    #def set_l2_strength(self, l2_strength):
    #    self.l2_strength = l2_strength