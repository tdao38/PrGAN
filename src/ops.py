import tensorflow.compat.v1 as tf
import tensorflow
import matplotlib.image as mpimg
import numpy as np
import scipy.misc
import sys
import re
from functools import reduce

class BatchNormalization(object):
    def __init__(self, shape, name, decay=0.9, epsilon=1e-5):
        with tf.compat.v1.variable_scope(name):
            self.beta = tf.Variable(tf.constant(0.0, shape=shape), name="beta") # offset
            self.gamma = tf.Variable(tf.constant(1.0, shape=shape), name="gamma") # scale
            self.ema = tf.train.ExponentialMovingAverage(decay=decay)
            self.epsilon = epsilon

    def __call__(self, x, train):
        self.train = train
        n_axes = len(x.get_shape()) - 1
        batch_mean, batch_var = tf.nn.moments(x, [0,1,2]) # range(n_axes)
        mean, variance = self.ema_mean_variance(batch_mean, batch_var)
        return tf.nn.batch_normalization(x, mean, variance, self.beta, self.gamma, self.epsilon)

    def ema_mean_variance(self, mean, variance):
        def with_update():
            with tf.variable_scope(tf.get_variable_scope(), reuse=tf.AUTO_REUSE):
                ema_apply = self.ema.apply([mean, variance])
            with tf.control_dependencies([ema_apply]):
                return tf.identity(mean), tf.identity(variance)
        return tf.cond(self.train, with_update, lambda: (self.ema.average(mean), self.ema.average(variance)))


def flatten(t) : return tf.reshape(t, [1, -1])


def rot_matrix(s):
    i = tf.cast((s+1)*4.5, 'int32')
    vp = tf.constant([0, np.pi/4.0, np.pi/2.0, 3*np.pi/4.0, np.pi, 5*np.pi/4.0, 3*np.pi/2.0, 7*np.pi/4.0, np.pi/2.0])
    hp = tf.constant([0, 0, 0, 0, 0, 0, 0, 0, np.pi/2.0])

    theta = tf.gather(vp, i)
    phi = tf.gather(hp, i)

    #theta = (s[0] + 1.0) * np.pi
    #phi = s[1] * np.pi/2.0

    sin_theta = tf.sin(theta)
    cos_theta = tf.cos(theta)
    sin_phi = tf.sin(phi)
    cos_phi = tf.cos(phi)

    ry = [[cos_theta, 0, sin_theta], [0, 1, 0], [-sin_theta, 0, cos_theta]]
    rx = [[1, 0, 0], [0, cos_phi, -sin_phi], [0, sin_phi, cos_phi]]

    return tf.matmul(rx, ry)
    #return ry

'''This is a hack. One day tensorflow will have gather_nd properly implemented with backprop.
Until then, use this function'''
def gather_nd(params, indices, name=None):
    shape = params.get_shape().as_list()
    rank = len(shape)
    flat_params = tf.reshape(params, [-1])
    multipliers = [reduce(lambda x, y: x*y, shape[i+1:], 1) for i in range(0, rank)]
    indices_unpacked = tf.unstack(tf.transpose(indices, [rank - 1] + [x for x in range(0, rank - 1)], name))
    flat_indices = sum([a*b for a,b in zip(multipliers, indices_unpacked)])
    return tf.gather(flat_params, flat_indices, name=name)


def grid_coord(h, w, d):
    xl = tf.linspace(-1.0, 1.0, w)
    yl = tf.linspace(-1.0, 1.0, h)
    zl = tf.linspace(-1.0, 1.0, d)

    xs, ys, zs = tf.meshgrid(xl, yl, zl, indexing='ij')
    g = tf.concat([flatten(xs), flatten(ys), flatten(zs)], 0)
    return g


def project(v, tau=1):
    p = tf.reduce_sum(v, 2)
    p = tf.ones_like(p) - tf.exp(-p*tau)
    return tf.reverse(tf.transpose(p), [True, False])


def get_voxel_values(v, xs, ys, zs):
    idxs = tf.cast(tf.stack([xs, ys, zs], axis=1), 'int32')
    idxs = tf.clip_by_value(idxs, 0, v.get_shape()[0])
    idxs = tf.expand_dims(idxs, 0)
    return gather_nd(v, idxs)


def resample_voxels(v, xs, ys, zs, method="trilinear"):
    
    if method == "trilinear":
        floor_xs = tf.floor(tf.clip_by_value(xs, 0, 64))
        floor_ys = tf.floor(tf.clip_by_value(ys, 0, 64))
        floor_zs = tf.floor(tf.clip_by_value(zs, 0, 64))

        ceil_xs = tf.ceil(tf.clip_by_value(xs, 0, 64))
        ceil_ys = tf.ceil(tf.clip_by_value(ys, 0, 64))
        ceil_zs = tf.ceil(tf.clip_by_value(zs, 0, 64))

        final_value =( tf.abs((xs-floor_xs)*(ys-floor_ys)*(zs-floor_zs))*get_voxel_values(v, ceil_xs, ceil_ys, ceil_zs) + 
                       tf.abs((xs-floor_xs)*(ys-floor_ys)*(zs-ceil_zs))*get_voxel_values(v, ceil_xs, ceil_ys, floor_zs) +
                       tf.abs((xs-floor_xs)*(ys-ceil_ys)*(zs-floor_zs))*get_voxel_values(v, ceil_xs, floor_ys, ceil_zs) +
                       tf.abs((xs-floor_xs)*(ys-ceil_ys)*(zs-ceil_zs))*get_voxel_values(v, ceil_xs, floor_ys, floor_zs) +
                       tf.abs((xs-ceil_xs)*(ys-floor_ys)*(zs-floor_zs))*get_voxel_values(v, floor_xs, ceil_ys, ceil_zs) +
                       tf.abs((xs-ceil_xs)*(ys-floor_ys)*(zs-ceil_zs))*get_voxel_values(v, floor_xs, ceil_ys, floor_zs) +
                       tf.abs((xs-ceil_xs)*(ys-ceil_ys)*(zs-floor_zs))*get_voxel_values(v, floor_xs, floor_ys, ceil_zs) +
                       tf.abs((xs-ceil_xs)*(ys-ceil_ys)*(zs-ceil_zs))*get_voxel_values(v, floor_xs, floor_ys, floor_zs)
                     )
        return final_value
    
    elif method == "nearest":
        r_xs = tf.round(xs)
        r_ys = tf.round(ys)
        r_zs = tf.round(zs)
        return get_voxel_values(v, r_xs, r_ys, r_zs)
    
    else:
        raise NameError(method)
  
  
def transform_volume(v, t):
    height = int(v.get_shape()[0])
    width = int(v.get_shape()[1])
    depth = int(v.get_shape()[2])
    grid = grid_coord(height, width, depth)
    
    xs = grid[0, :]
    ys = grid[1, :]
    zs = grid[2, :]
    
    idxs_f = tf.transpose(tf.stack([xs, ys, zs]))
    idxs_f = tf.matmul(idxs_f, t)
    
    xs_t = (idxs_f[:, 0] + 1.0) * float(width) / 2.0
    ys_t = (idxs_f[:, 1] + 1.0) * float(height) / 2.0
    zs_t = (idxs_f[:, 2] + 1.0) * float(depth) / 2.0
    
    return tf.reshape(resample_voxels(v, xs_t, ys_t, zs_t, method='trilinear'), v.get_shape())


class batch_norm_linear(object):
    """Code modification of http://stackoverflow.com/a/33950177"""
    def __init__(self, epsilon=1e-5, momentum=0.9, name="batch_norm"):
        with tf.variable_scope(name):
            self.epsilon = epsilon
            self.momentum = momentum

            self.ema = tf.train.ExponentialMovingAverage(decay=self.momentum)
            self.name = name

    def __call__(self, x, train=True):
        shape = x.get_shape().as_list()

        if train:
            with tf.variable_scope(self.name):
                self.beta = tf.get_variable("beta", [shape[-1]],
                                            initializer=tf.constant_initializer(0.))
                self.gamma = tf.get_variable("gamma", [shape[-1]],
                                             initializer=tf.random_normal_initializer(1., 0.02))

                # Remove 2 from list to work with fully connected
                # TODO: fix this issue
                batch_mean, batch_var = tf.nn.moments(x, [0, 1], name='moments')
                ema_apply_op = self.ema.apply([batch_mean, batch_var])
                self.ema_mean, self.ema_var = self.ema.average(batch_mean), self.ema.average(batch_var)

                with tf.control_dependencies([ema_apply_op]):
                    mean, var = tf.identity(batch_mean), tf.identity(batch_var)
        else:
            mean, var = self.ema_mean, self.ema_var

        normed = tf.nn.batch_norm_with_global_normalization(
                x, mean, var, self.beta, self.gamma, self.epsilon, scale_after_normalization=True)

        return normed


def minibatch(input, num_kernels=300, kernel_dim=50, batch_size=64):
#
#    n_kernels = num_kernels
#    dim_per_kernel = kernel_dim
#    x = linear(input, input.get_shape()[1], n_kernels * dim_per_kernel, scope="d_mbd")
#    activation = tf.reshape(x, (batch_size, n_kernels, dim_per_kernel))
#
#    big = np.zeros((batch_size, batch_size), dtype='float32')
#    big += np.eye(batch_size)
#    big = tf.expand_dims(big, 1)
#
#    abs_dif = tf.reduce_sum(tf.abs(tf.expand_dims(activation, 3) - tf.expand_dims(tf.transpose(activation, [1, 2, 0]), 0)), 2)
#    mask = 1. - big
#    masked = tf.exp(-abs_dif) * mask
#
#    def half(tens, second):
#        m, n, _ = tens.get_shape()
#        m = int(m)
#        n = int(n)
#        return tf.slice(tens, [0, 0, second * batch_size], [m, n, batch_size])
#    # TODO: speedup by allocating the denominator directly instead of constructing it by sum
#    #       (current version makes it easier to play with the mask and not need to rederive
#    #        the denominator)
#    f1 = tf.reduce_sum(half(masked, 0), 2) / tf.reduce_sum(half(mask, 0))
#    f2 = tf.reduce_sum(half(masked, 1), 2) / tf.reduce_sum(half(mask, 1))
#
#    minibatch_features = [f1, f2]
#
#    x = tf.concat(1, [input] + minibatch_features)
#    return x

    x = linear(input, input.get_shape()[1], num_kernels * kernel_dim)
    activation = tf.reshape(x, (-1, num_kernels, kernel_dim))
    diffs = tf.expand_dims(activation, 3) - \
        tf.expand_dims(tf.transpose(activation, [1, 2, 0]), 0)
    abs_diffs = tf.reduce_sum(tf.abs(diffs), 2)
    minibatch_features = tf.reduce_sum(tf.exp(-abs_diffs), 2)
    return tf.concat(1, [input, minibatch_features])


def lrelu(x, leak=0.2, name="lrelu"):
    with tf.variable_scope(name):
        f1 = 0.5 * (1 + leak)
        f2 = 0.5 * (1 - leak)
        return f1 * x + f2 * abs(x)


def linear(x, n_input, n_output, activation=None, scope=None):
    with tf.variable_scope(scope or "linear"):
        w = tf.get_variable(
            name='w',
            shape=[n_input, n_output],
            initializer=tf.random_normal_initializer(mean=0.0, stddev=0.1))
        b = tf.get_variable(
            name='b',
            shape=[n_output],
            initializer=tf.constant_initializer())
        h = tf.matmul(x, w) + b
        if activation is not None:
            h = activation(h)
        return h


def conv2d(input_, output_dim, k_h=5, k_w=5, d_h=2, d_w=2, stddev=0.02,
           name="conv2d"):
    with tf.variable_scope(name):
        w = tf.get_variable('w', [k_h, k_w, input_.get_shape()[-1], output_dim],
                            initializer=tf.truncated_normal_initializer(stddev=stddev))
        conv = tf.nn.conv2d(input_, w, strides=[1, d_h, d_w, 1], padding='SAME')

        biases = tf.get_variable('biases', [output_dim], initializer=tf.constant_initializer(0.0))
        conv = tf.reshape(tf.nn.bias_add(conv, biases), conv.get_shape())

        return conv


def conv3d(input_, output_dim, k_h=4, k_w=4, k_d=4, d_h=2, d_w=2, d_d=2, stddev=0.02,
           name="conv3d"):
    with tf.variable_scope(name):
        w = tf.get_variable('w', [k_d, k_h, k_w, input_.get_shape()[-1], output_dim],
                            initializer=tf.truncated_normal_initializer(stddev=stddev))
        conv = tf.nn.conv3d(input_, w, strides=[1, d_d, d_h, d_w, 1], padding='SAME')

        biases = tf.get_variable('biases', [output_dim], initializer=tf.constant_initializer(0.0))
        conv = tf.reshape(tf.nn.bias_add(conv, biases), conv.get_shape())

        return conv


# From DCGAN tensrorflow implementation
def deconv2d(input_, output_shape,
             k_h=4, k_w=4, d_h=2, d_w=2, stddev=0.02,
             name="deconv2d", with_w=False):
    with tf.variable_scope(name):
        # filter : [height, width, output_channels, in_channels]
        w = tf.get_variable('w', [k_h, k_h, output_shape[-1], input_.get_shape()[-1]],
                            initializer=tf.random_normal_initializer(stddev=stddev))

        try:
            deconv = tf.nn.conv2d_transpose(input_, w, output_shape=output_shape,
                                            strides=[1, d_h, d_w, 1])

        # Support for verisons of TensorFlow before 0.7.0
        except AttributeError:
            deconv = tf.nn.deconv2d(input_, w, output_shape=output_shape,
                                    strides=[1, d_h, d_w, 1])

        biases = tf.get_variable('biases', [output_shape[-1]], initializer=tf.constant_initializer(0.0))
        deconv = tf.reshape(tf.nn.bias_add(deconv, biases), deconv.get_shape())

        if with_w:
            return deconv, w, biases
        else:
            return deconv


def deconv3d(input_, output_shape,
             k_h=4, k_w=4, k_d=4,
             d_h=2, d_w=2, d_d=2,
             stddev=0.02,
             name='deconv3d',
             with_w=False):
    with tf.variable_scope(name):
        w = tf.get_variable('w', [k_d, k_h, k_w, int(output_shape[-1]), input_.get_shape()[-1]],
                            initializer=tf.random_normal_initializer(stddev=stddev))
        try:
            deconv = tf.nn.conv3d_transpose(input_, w, output_shape=tf.cast(output_shape, dtype=tf.int32), strides=[1, d_d, d_h, d_w, 1])
        except AttributeError:
            print ("This tensorflow version does not supprot tf.nn.conv3d_transpose.")

        biases = tf.get_variable('biases', int(output_shape[-1]), initializer=tf.constant_initializer(0.0))
        deconv = tf.nn.bias_add(deconv, biases)

        if with_w:
            return deconv, w, biases
        else:
            return deconv


def l2norm_sqrd(a, b): return tf.reduce_sum(tf.pow(a-b, 2), 1)


def l2(a, b): return tf.reduce_mean(tf.pow(a-b, 2))


def show_graph_operations():
    operations = [op.name for op in tf.get_default_graph().get_operations()]
    for o in operations:
        print(o)


def load_flatten_imgbatch(img_paths):
    images = []
    for path in img_paths:
        images.append(mpimg.imread(path).flatten())
    return np.array(images)


def load_imgbatch(img_paths, color=True):
    images = []
    if color:
        for path in img_paths:
            images.append(mpimg.imread(path)[:, :, 0:3])
    else:
        for path in img_paths:
            img = mpimg.imread(path)
            img = np.reshape(img, (img.shape[0], img.shape[1], 1))
            images.append(img)
    return images


def load_voxelbatch(voxel_paths):
    voxels = []
    for path in voxel_paths:
        voxels.append(np.load(path))
    return (np.array(voxels))


def save_voxels(voxels, folder):
    basename="/volume{}.npy"
    for i in range(voxels.shape[0]):
        np.save(folder+basename.format(i), voxels[i, :, :, :])


def tryint(s):
    try:
        return int(s)
    except:
        return s


def alphanum_key(s):
    """ Turn a string into a list of string and number chunks.
        "z23a" -> ["z", 23, "a"]
    """
    return [tryint(c) for c in re.split('([0-9]+)', s)]


def save_separate_images(images, folder):
    basename = "/image{}.png"
    for i in range(images.shape[0]):
        mpimg.imsave(folder+basename.format(i), images[i, :, :, 0], cmap='gray')


def save_images(images, size, image_path):
    return imsave(inverse_transform(images), size, image_path)


def save_image(image, image_path):
    return scipy.misc.imsave(image_path, image[0, :, :, 0])


def imread(path):
    return scipy.misc.imread(path).astype(np.float)


def merge_images(images, size):
    return inverse_transform(images)


def merge(images, size):
    h, w = images.shape[1], images.shape[2]
    img = np.zeros((h * size[0], w * size[1], 3))

    for idx, image in enumerate(images):
        i = idx % size[1]
        j = idx / size[1]
        img[j*h:j*h+h, i*w:i*w+w, :] = image

    return img


def imsave(images, size, path):
    return mpimg.imsave(path, merge(images, size))


def center_crop(x, crop_h, crop_w=None, resize_w=64):
    if crop_w is None:
        crop_w = crop_h
    h, w = x.shape[:2]
    j = int(round((h - crop_h)/2.))
    i = int(round((w - crop_w)/2.))
    return scipy.misc.imresize(x[j:j+crop_h, i:i+crop_w],
                               [resize_w, resize_w])


def transform(image, npx=64, is_crop=True):
    # npx : # of pixels width/height of image
    if is_crop:
        cropped_image = center_crop(image, npx)
    else:
        cropped_image = image
    return np.array(cropped_image)


def inverse_transform(images):
    return np.clip(images, 0, 1)


def progress(count, total, suffix=''):
    bar_len = 60
    filled_len = int(round(bar_len * count / float(total)))

    percents = round(100.0 * count / float(total), 1)
    bar = '=' * filled_len + '-' * (bar_len - filled_len)

    sys.stdout.write('[%s] %s%s ...%s\r' % (bar, percents, '%', suffix))
    sys.stdout.flush()  # As suggested by Rom Ruben
