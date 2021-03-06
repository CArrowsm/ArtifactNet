import os
from argparse import ArgumentParser
from collections import OrderedDict

import numpy as np
import torch
import torch.nn as nn



def get_seq_out_shape(network, in_shape=[8, 256, 256], in_channels=1) :
    """Calculate the output shape of a tensor passed through network.
    Parameters :
    ------------
    network :  A torch.nn.Sequential object containing the convolutional
               layers of a CNN. This function will perform a forward pass
               through the network and return the shape of the resulting tensor.
    in_shape : The input shape of the tensor. Should have format
               [channels, depth, height, width] for 3D images or
               [channels, height, width] for 2D images.
    in_channels (int): The number of channels for the input image. Default is 1.
    Returns :
    ---------
    The shape of the tensor after a forward pass through network.
    """
    # out = ((in_shape + 2 * padding - dilation * (kernel_size - 1) - 1) / stride) + 1
    in_shape = [1, in_channels] + in_shape # Add in_channels and arbitrary batch size
    X = torch.randn(*in_shape)
    with torch.no_grad() :
        for layer in network :
            X = layer(X)

    # out_shape = network(X).shape
    out_shape = np.array(X.shape)
    return out_shape






class CNN_2D(nn.Module):
    def __init__(self, in_channels=1, out_channels=1, init_features=64):
        super(CNN_2D, self).__init__()

        """ Use architecture from Mattea's DA detection paper """
        # PyTorch's conv2d takes the following form:
        # initialization:   (channels_in, channels_out, kernel_size)
        # Input :           (batch_size, Channels_in, H, W)

        self.pool = nn.MaxPool2d(2, 2) # (kernel_size, stride)
        self.LRelu = nn.LeakyReLU(0.2)

        self.filters = init_features
        self.conv1 = nn.Conv2d(in_channels, self.filters, 5, padding=2)
        self.conv1_bn = nn.BatchNorm2d(self.filters)

        self.conv2 = nn.Conv2d(self.filters, self.filters*2, 3, padding=1)
        self.conv2_bn = nn.BatchNorm2d(self.filters*2)

        self.conv3 = nn.Conv2d(self.filters*2, self.filters*4, 3, padding=1)
        self.conv3_bn = nn.BatchNorm2d( self.filters*4)

        self.conv4 = nn.Conv2d(self.filters*4, self.filters*8, 3, padding=1)
        self.conv4_bn = nn.BatchNorm2d(self.filters*8)

        self.conv5 = nn.Conv2d(self.filters*8, self.filters*16, 3, padding=1)
        self.conv5_bn = nn.BatchNorm2d(self.filters*16)

        self.avgPool = nn.AvgPool2d(2, 2)

        self.fc3 = nn.Linear(self.filters*16 * 8 * 8, out_channels)

        # self.softmax = torch.nn.Softmax(dim=1)
        #
        # self.sigmoid = torch.nn.Sigmoid()

    def forward(self, X):
        X = self.pool(self.conv1_bn(self.LRelu(self.conv1(X))))
        X = self.pool(self.conv2_bn(self.LRelu(self.conv2(X))))
        X = self.pool(self.conv3_bn(self.LRelu(self.conv3(X))))
        X = self.pool(self.conv4_bn(self.LRelu(self.conv4(X))))
        X = self.conv5_bn(self.LRelu(self.conv5(X)))
        X = self.avgPool(X)

        # X.view(-1, Y) reshapes X to shape (batch_size, Y) for FC layer
        X = X.view(-1, self.filters*16 * 8 * 8)
        X = self.fc3(X)

        # Constrain output of model to (0, 1)
        # X = self.sigmoid(X)

        return X




class CNN_3D(nn.Module):
    def __init__(self, in_channels=1, out_channels=1, init_features=64):
        super(CNN_3D, self).__init__()

        """ Use architecture from Mattea's DA detection paper """
        # PyTorch's conv2d takes the following form:
        # initialization:   (channels_in, channels_out, kernel_size)
        # Input :           (batch_size, Channels_in, H, W)

        self.pool = nn.MaxPool3d(2, 2) # (kernel_size, stride)
        self.LRelu = nn.LeakyReLU(0.2)

        self.filters = init_features
        self.conv1 = nn.Conv3d(in_channels, self.filters, 5, padding=2)
        self.conv1_bn = nn.BatchNorm3d(self.filters)

        self.conv2 = nn.Conv3d(self.filters, self.filters*2, 3, padding=1)
        self.conv2_bn = nn.BatchNorm3d(self.filters*2)

        self.conv3 = nn.Conv3d(self.filters*2, self.filters*4, 3, padding=1)
        self.conv3_bn = nn.BatchNorm3d( self.filters*4)

        # self.conv4 = nn.Conv3d(self.filters*4, self.filters*8, 3, padding=1)
        # self.conv4_bn = nn.BatchNorm3d(self.filters*8)

        self.conv5 = nn.Conv3d(self.filters*4, self.filters*8, 3, padding=1)
        self.conv5_bn = nn.BatchNorm3d(self.filters*8)

        self.avgPool = nn.AvgPool3d(2, 2)

        self.fc3 = nn.Linear(self.filters*8 * 16 * 16, out_channels)

    def forward(self, X):                             # X.shape = (N,    1, 16, 256, 256)
        X = self.pool(self.conv1_bn(self.LRelu(self.conv1(X)))) # (N,   64,  8, 128, 128)
        X = self.pool(self.conv2_bn(self.LRelu(self.conv2(X)))) # (N,  128,  4,  64,  64)
        X = self.pool(self.conv3_bn(self.LRelu(self.conv3(X)))) # (N,  256,  2,  32,  32)
        # X = self.pool(self.conv4_bn(self.LRelu(self.conv4(X)))) # (N,  512,  2,  16,  16)
        X = self.conv5_bn(self.LRelu(self.conv5(X)))            # (N, 512,   2,  32,  32)
        X = self.avgPool(X)                                     # (N, 512,  1,   16,   16)


        # X.view(-1, Y) reshapes X to shape (batch_size, Y) for FC layer
        X = X.view(-1, self.filters*8 * 16 * 16)
        X = self.fc3(X)

        return X


class CNNnLayer(nn.Module) :
    """A 3D CNN with variable depth"""
    def __init__(self, in_channels=1, out_channels=1, init_features=64, n_layers=4, in_shape=[8, 256, 256]):
        """
        Parameters:
            in_channels (int) :  Number of input channels (default=1).
            n_filters (int):     The number of filters to use in the last
                                 concolutional layer (default=64).
            n_layers (int):      The number of convolutional layers to use
                                 (default = 4).
            in_shape (list) :    The shape of the input tensor. Used to calculate
                                 the size of the linear layer.
        """
        super(CNNnLayer, self).__init__()

        # Parameters for convolutional layers
        ks = 3             # Kernel size
        pads = 1           # Padding size
        s = [1, 1, 1]      # Convolution stride
        normfunc = nn.BatchNorm3d
        lrelu = nn.LeakyReLU(0.2)
        pool = nn.MaxPool3d(2, 2)

        # Create first convolutional layer
        net_list = [nn.Conv3d(in_channels=in_channels, out_channels=init_features,
                               kernel_size=5, stride=s, padding=2),
                    normfunc(init_features), lrelu, pool]

        # Add middle layers
        for i in range(1, n_layers) :
            if i == n_layers - 1 :          # Middle pooling layers are max pool
                pool = nn.AvgPool3d(2, 2)   # Last pooling layer is avg pool

            in_ch = min(init_features * (2 ** (i - 1)), 512)
            out_ch = min(init_features * (2 ** i), 512)
            net_list += [nn.Conv3d(in_channels=in_ch, out_channels=out_ch,
                                   kernel_size=ks, stride=s, padding=pads),
                         normfunc(out_ch), lrelu, pool]

        self.net = nn.Sequential(*net_list)

        # Get output size of tensor from conv layers
        out_shape = get_seq_out_shape(self.net, in_shape=in_shape, in_channels=in_channels)

        # Create final conv layer
        self.fc_in_size = int(np.prod(out_shape)) # Length of flattened array from last conv layer
        self.fc = nn.Linear(self.fc_in_size, out_channels)


    def forward(self, X) :
        """ Forward pass through the network"""
        X = self.net(X)

        # X.view(-1, Y) reshapes X to shape (batch_size, Y) for FC layer
        X = X.view(-1, self.fc_in_size)
        X = self.fc(X)
        return X




class VGG2D(nn.Module):
    """Implementation of 2D VGG-16 with variable depth."""
    def __init__(self, in_channels=1, out_channels=1, n_filters=64, n_layers=5):
        """
        Parameters :
            in_channels (int) :  Number of input channels to use (use this as z-axis).
            out_channels (int) : Number of output channels for scalar output.
            n_filters (int) :    Number of filters for first conv layer. Subsequent
                                 layers i will use (2**i)*n_filters filters.
            n_layers (int) :     Number of 2-convolution blocks to use. i.e. use
                                 n_layers=5 for VGG16.
        """
        super(VGG2D, self).__init__()

        # Define layers without learnable parameters
        self.relu = nn.LeakyReLU(0.2)
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)

        # Define sequential networks with first layer
        net = self.conv_block(in_channels, n_filters)
        net += [self.pool]
        filters = n_filters

        # Define layers of VGG
        for i in range(n_layers) :
            in_ch = filters    # Output channels from previous block
            filters = min((2 ** i) * n_filters, 512) # Output channels from this block

            # Build conv block
            net += self.conv_block(in_ch, filters) # Two conv-bnorm-relu layers
            net += [self.pool]                      # Pooling layer

        self.network = nn.Sequential(*net)
        self.fc1 = nn.Linear(filters * 8 * 8, out_channels)
        # self.softmax = nn.Softmax()
        # self.sigmoid = nn.Sigmoid()


    def conv_block(self, in_ch, out_ch, batch_norm=True, leaky=True) :
        """Defines a block of 2 convolutional layers"""
        ### First conv layer in block ###
        block = [nn.Conv2d(in_channels=in_ch, out_channels=out_ch, kernel_size=3,
                 stride=1, padding=1)]
        if batch_norm :
            block += [nn.BatchNorm2d(out_ch)]
        block += [self.relu]

        ### Second conv layer in block ###
        block += [nn.Conv2d(in_channels=out_ch, out_channels=out_ch, kernel_size=3,
                 stride=1, padding=1)]
        if batch_norm :
            block += [nn.BatchNorm2d(out_ch)]
        block += [self.relu]

        return block

    def forward(self, X) :
        X = self.network(X)
        X = X.view(-1, 512 * 8 * 8)
        X = self.fc1(X)
        X = self.sigmoid(X)
        return X



class PatchGAN_3D(nn.Module) :
    """A 3D PatchGAN """
    def __init__(self, input_channels=1, out_size=1, n_filters=64):
        """
        Parameters:
            input_channels (int) : Number of input channels (default=1).
            out_size (int) :       The shape of the output tensor. The output
                                   will be cubic with shape
                                   (out_size, out_size, out_size). Default=1.
            n_filters :            The number of filters to use in the last
                                   concolutional layer (default=64).
        """
        super(PatchGAN_3D, self).__init__()

        # Parameters for convolutional layers
        ks = 4              # Kernel size
        pads = 1            # Padding size
        s = [1, 2, 2]       # Convolution stride
        use_bias = True     # Include learnable bias term
        normfunc = nn.InstanceNorm3d

        self.conv1 = nn.Conv3d(in_channels=input_channels, out_channels=n_filters,
                               kernel_size=ks, stride=s, padding=pads)
        self.conv2 = nn.Conv3d(in_channels=n_filters, out_channels=n_filters * 2,
                               kernel_size=ks, stride=s, padding=pads, bias=use_bias)
        self.conv3 = nn.Conv3d(in_channels=n_filters * 2, out_channels=n_filters * 4,
                               kernel_size=ks, stride=s, padding=pads, bias=use_bias)
        self.conv4 = nn.Conv3d(in_channels=n4_filters * 4, out_channels=n_filters * 8,
                               kernel_size=ks, stride=s, padding=pads, bias=use_bias)

        self.convf = nn.Conv3d(in_channels=n_filters * 8, out_channels=1,
                               kernel_size=[16, 18,  18], stride=s, padding=0, bias=True)

        self.inorm2 = normfunc(n_filters * 2, affine=False)
        self.inorm3 = normfunc(n_filters * 4, affine=False)
        self.inorm4 = normfunc(n_filters * 8, affine=False)

        self.Lrelu1 = nn.LeakyReLU(0.2, True)
        self.Lrelu2 = nn.LeakyReLU(0.2, True)
        self.Lrelu3 = nn.LeakyReLU(0.2, True)
        self.Lrelu4 = nn.LeakyReLU(0.2, True)

        self.sigmoid = nn.Sigmoid()

    def forward(self, X) :
        # Assume batch_size = N and X.shape = (N,   1, 20, 300, 300)
        # Layer 1
        X = self.conv1(X)                    # (N,  64, 19, 150, 150)
        X = self.Lrelu1(X)                   # (N,  64, 19, 150, 150)

        # Layer 2
        X = self.conv2(X)                    # (N, 128,  18, 75,  75)
        X = self.Lrelu2(X)                   # (N, 128,  18, 75,  75)
        X = self.inorm2(X)                   # (N, 128,  18, 75,  75)

        # Layer 3
        X = self.conv3(X)                    # (N, 256,  17, 37,  37)
        X = self.Lrelu3(X)                   # (N, 256,  17, 37,  37)
        X = self.inorm3(X)                   # (N, 256,  17, 37,  37)

        # Layer 4
        X = self.conv4(X)                    # (N, 512,  16, 18,  18)
        X = self.Lrelu4(X)                   # (N, 512,  16, 18,  18)
        X = self.inorm4(X)                   # (N, 512,  16, 18,  18)

        # Final convolutional layer to make scalar output
        X = self.convf(X)                   # (N,   1,  1,   1,   1)

        return X



class PatchGAN_NLayer(nn.Module) :
    """A 3D PatchGAN with variable depth"""
    def __init__(self, input_channels=1, out_size=1, n_filters=64, n_layers=4, norm="instance",
                 input_shape=[20, 300, 300]):
        """
        Parameters:
            input_channels (int) : Number of input channels (default=1).
            out_size (int) :       The shape of the output tensor. The output
                                   will be cubic with shape
                                   (out_size, out_size, out_size). Default=1.
            n_filters :            The number of filters to use in the last
                                   concolutional layer (default=64).
            n_layers :             The number of convolutional layers to use
                                   (default = 4).
            norm :                 Type of normalization to use (can be either
                                   'instance' or 'batch').
            input_shape :          Shape of the input tensor (input image).
        """
        super(PatchGAN_NLayer, self).__init__()

        # Parameters for convolutional layers
        ks = 4                    # Kernel size
        pads = 1                  # Padding size
        s = [1, 2, 2]             # Convolution stride
        use_bias = True
        normfunc = nn.InstanceNorm3d


        # Build up layers sequentially
        net_list = [nn.Conv3d(in_channels=input_channels, out_channels=n_filters,
                               kernel_size=ks, stride=s, padding=pads, bias=use_bias),
                    nn.LeakyReLU(0.2, True)]
        out_shape = conv_out_shape(input_shape, kernel_size=ks, stride=s, padding=pads)

        # Add middle layers
        for i in range(1, n_layers) :
            in_channels = n_filters * (2 ** (i - 1))
            out_filters = n_filters * (2 ** i)
            net_list += [nn.Conv3d(in_channels=in_channels, out_channels=out_filters,
                                   kernel_size=ks, stride=s, padding=pads, bias=use_bias),
                         normfunc(out_filters, affine=False),
                         nn.LeakyReLU(0.2, True)]
            # Calculate shape of output tensor from this layer
            out_shape = conv_out_shape(out_shape, kernel_size=ks, stride=s, padding=pads)

        # Add final conv layer
        net_list += [nn.Conv3d(in_channels=out_filters, out_channels=1,
                               kernel_size=[16, 18,  18], stride=s, padding=0, bias=use_bias)]
        out_shape = conv_out_shape(out_shape, kernel_size=ks, stride=s, padding=pads)



        self.net = nn.Sequential(*net_list)

    def forward(self, X) :
        X = self.net(X)
        return X
