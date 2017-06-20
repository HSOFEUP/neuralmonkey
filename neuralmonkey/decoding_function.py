"""
Module which implements decoding functions using multiple attentions
for RNN decoders.

See http://arxiv.org/abs/1606.07481
"""

import tensorflow as tf
from neuralmonkey.nn.projection import linear
from neuralmonkey.vocabulary import STEP_TOKEN_INDEX

class Attention(object):
    # pylint: disable=unused-argument,too-many-instance-attributes
    # pylint: disable=too-many-arguments

    # For maintaining the same API as in CoverageAttention

    def __init__(self, attention_states, scope,
                 input_weights=None, attention_fertility=None) -> None:
        """Create the attention object.

        Args:
            attention_states: A Tensor of shape (batch x time x state_size)
                              with the output states of the encoder.
            scope: The name of the variable scope in the graph used by this
                   attention object.
            input_weights: (Optional) The padding weights on the input.
            attention_fertility: (Optional) For the Coverage attention
                compatibilty, maximum fertility of one word.
        """
        self.scope = scope
        self.logits_in_time = []  # type: List[tf.Tensor]
        self.attentions_in_time = []  # type: List[tf.Tensor]
        self.attention_states = attention_states
        self.input_weights = input_weights

        with tf.variable_scope(scope):
            self.attn_size = attention_states.get_shape()[2].value

            # To calculate W1 * h_t we use a 1-by-1 convolution, need to
            # reshape before.
            self.att_states_reshaped = tf.expand_dims(self.attention_states, 2)

            # Size of query vectors for attention.
            self.attention_vec_size = self.attn_size

            # This variable corresponds to Bahdanau's U_a in the paper
            k = tf.get_variable(
                "AttnW", [1, 1, self.attn_size, self.attention_vec_size],
                initializer=tf.random_normal_initializer(stddev=0.001))

            self.hidden_features = tf.nn.conv2d(self.att_states_reshaped, k,
                                                [1, 1, 1, 1], "SAME")

            # pylint: disable=invalid-name
            # see comments on disabling invalid names below
            self.v = tf.get_variable(
                name="AttnV",
                shape=[self.attention_vec_size],
                initializer=tf.random_normal_initializer(stddev=.001))
            self.v_bias = tf.get_variable(
                "AttnV_b", [], initializer=tf.constant_initializer(0))

    def attention(self, query_state, input_idx) -> tf.Tensor:
        """Put attention masks on att_states_reshaped
           using hidden_features and query.
        """

        with tf.variable_scope(self.scope + "/Attention") as varscope:
            # Sort-of a hack to get the matrix (bahdanau's W_a) in the linear
            # projection to be initialized this way. The biases are initialized
            # as zeros
            varscope.set_initializer(
                tf.random_normal_initializer(stddev=0.001))
            y = linear(query_state, self.attention_vec_size, scope=varscope)
            y = tf.reshape(y, [-1, 1, 1, self.attention_vec_size])

            # pylint: disable=invalid-name
            # code copied from tensorflow. Suggestion: rename the variables
            # according to the Bahdanau paper
            s = self.get_logits(y, input_idx)

            if self.input_weights is None:
                a = tf.nn.softmax(s)
            else:
                a_all = tf.nn.softmax(s) * self.input_weights
                norm = tf.reduce_sum(a_all, 1, keep_dims=True) + 1e-8
                a = a_all / norm

            self.logits_in_time.append(s)
            self.attentions_in_time.append(a)

            # Now calculate the attention-weighted vector d.
            d = tf.reduce_sum(tf.expand_dims(tf.expand_dims(a, -1), -1)
                              * self.att_states_reshaped, [1, 2])

            return tf.reshape(d, [-1, self.attn_size])

    def get_logits(self, y, input_idx):
        # Attention mask is a softmax of v^T * tanh(...).
        return tf.reduce_sum(
            self.v * tf.tanh(self.hidden_features + y), [2, 3]) + self.v_bias


class CoverageAttention(Attention):

    # pylint: disable=too-many-arguments
    # Great objects require great number of parameters
    def __init__(self, attention_states, scope,
                 input_weights=None, attention_fertility=5):

        super(CoverageAttention, self).__init__(
            attention_states, scope,
            input_weights=input_weights,
            attention_fertility=attention_fertility)

        self.coverage_weights = tf.get_variable("coverage_matrix",
                                                [1, 1, 1, self.attn_size])
        self.fertility_weights = tf.get_variable("fertility_matrix",
                                                 [1, 1, self.attn_size])
        self.attention_fertility = attention_fertility

        self.fertility = 1e-8 + self.attention_fertility * tf.sigmoid(
            tf.reduce_sum(self.fertility_weights * self.attention_states, [2]))

    def get_logits(self, y, input_idx):
        coverage = sum(
            self.attentions_in_time) / self.fertility * self.input_weights

        logits = tf.reduce_sum(
            self.v * tf.tanh(
                self.hidden_features + y + self.coverage_weights *
                tf.expand_dims(tf.expand_dims(coverage, -1), -1)),
            [2, 3])

        return logits

class MonotonicAttention(Attention):

    def __init__(self, attention_states, scope,
                 input_weights=None, attention_fertility=None):

        super(MonotonicAttention, self).__init__(
            attention_states, scope,
            input_weights=input_weights,
            attention_fertility=attention_fertility)

        self.batch_size = tf.shape(attention_states)[0]
        self.sequence_len = tf.shape(attention_states)[1]
        self.indices = tf.zeros([self.batch_size],
                                dtype=tf.int32,
                                name="indice_matrix")
        self.indices = self.indices + 1

    def get_logits(self, y, input_idx):
        self.indices = tf.add(
            self.indices,
            tf.cast(tf.equal(input_idx, STEP_TOKEN_INDEX), tf.int32))
        logits = tf.sequence_mask(self.indices, self.sequence_len)
        logits = tf.logical_xor(
            logits,
            tf.sequence_mask(self.indices - 1, self.sequence_len))

        return tf.cast(logits, tf.float32)
