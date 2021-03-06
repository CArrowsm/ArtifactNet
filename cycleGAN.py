"""
This script trains a cycleGAN neural network to remove dental artifacts (DA)
from RadCure CT image volumes.

To begin training this model on H4H, run
$ sbatch scripts/train_cycleGAN.sh
"""

import os
import itertools
from argparse import ArgumentParser
from collections import OrderedDict

import matplotlib.pyplot as plt

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torchvision

from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import ModelCheckpoint


import torchvision.transforms as transforms
from torch.utils.data import DataLoader

import pytorch_lightning as pl

from config.options import get_args

from data.data_loader import load_image_data_frame, UnpairedDataset, PairedDataset
from data.transforms import AffineTransform, ToTensor, Normalize, HorizontalFlip

from models.generators import UNet3D, ResNetK, UNet2D, UNet3D_3layer
from models.discriminators import CNN_3D, PatchGAN_NLayer, CNNnLayer
from util.helper_functions import set_requires_grad
from util.loggers import TensorBoardCustom

from torch.optim.lr_scheduler import ReduceLROnPlateau, MultiStepLR

"""
This script trains a cycleGAN to remove dental artifacts from radcure images
using the pytorch-lightning framework. To run the script, just run python cycleGAN.py
on a GPU node on H4H.

Papers
-  https://junyanz.github.io/CycleGAN/
-  https://arxiv.org/pdf/1911.08105.pdf

CycleGAN Architecture:
---------------------
The aim of this netowrk is to map images from domain X (DA+) to images from
domain Y (DA-). Images from these domains are unpaired and do not have to be the
same length. For example:
X = {x_0, x_1, ..., x_N} and Y = {x_0, x_1, ..., x_M}
where x_i and y_i do not come from the same patient.

The network consits of two generators G_X and G_Y which attempt to perform the
mappings G_Y : X -> Y and G_X : Y -> X. Each of these is a 3D U-Net.

We also have two discriminators D_X and D_Y which try to real from fake images
in each class. For these we use the patchGAN discriminator architecture.
"""


""" MAIN PYTORCH-LIGHTNING MODULE """
class GAN(pl.LightningModule) :
    """
    Parameters :
    ------------
        hparams (dict) :
            Should include all hyperparameters as well as paths to data and CSV
            of labels.
    Attributes :
    ------------
        image_size (list, length of 3) :
            A list representing the 3D shape of the images to be to used, indexed
            as (z_size, y_size, x_size). To use a single CT slice, pass
            image_size=(1, y_size, x_size).
        dimension (int) :
            Whether to use a 3D or 2D network. If 2, the images will have shape
            (batch_size, z_size, y_size, x_size) and the z-axis will be used as
            the input-channels of a 2D network. If 3, the images will have shape
            (batch_size, 1, z_size, y_size, x_size) and a fully 3D convolutional
            network will be used.
    """

    def __init__(self, hparams=None):
        super(GAN, self).__init__()
        self.hparams = hparams
        self.image_size = hparams.image_size
        self.dimension = len(hparams.image_size)
        self.n_filters = hparams.n_filters
        self.cnn_layers = hparams.cnn_layers

        ### Initialize Networks ###
        # g_y maps X -> Y and generator_x maps Y -> X
        self.g_y = UNet3D_3layer(in_channels=1, out_channels=1, init_features=self.n_filters)
        self.g_x = UNet3D_3layer(in_channels=1, out_channels=1, init_features=self.n_filters)

        # One discriminator to identify real DA+ images, another for DA- images
        self.d_y = CNNnLayer(in_channels=1, out_channels=1, init_features=self.n_filters,
                             n_layers=self.cnn_layers, in_shape=self.image_size)
        self.d_x = CNNnLayer(in_channels=1, out_channels=1, init_features=self.n_filters,
                             n_layers=self.cnn_layers, in_shape=self.image_size)
        ### ------------------- ###

        # Put networks on GPUs
        self.gpu_check()

        # Define loss functions
        self.l1_loss  = nn.L1Loss(reduction="mean")
        self.adv_loss = nn.BCEWithLogitsLoss() # Compatible with amp 16-bit

        # Define loss term coefficients
        self.lam = 10.0   # Coefficient for cycle consistency loss
        self.idt = 25.0   # Coefficient for identity loss




    def gpu_check(self) :
        if torch.cuda.is_available() :
            self.n_gpus = torch.cuda.device_count()
            print(f"{self.n_gpus} GPUs are available")

            for i in range(self.n_gpus) :
                device_name = torch.cuda.get_device_name(i)
                print(f"### Device {i}: ###")
                print("Name: ", device_name)
                nbytes = torch.cuda.memory_allocated(device=i)
                print("Memory allocated: ", nbytes)


    def prepare_data(self) :
        """ Load the image file names, create dataset objects.
        Called automatically by pytorch lightning.
        """
        # Get train and test data sets
        # Import CSV containing DA labels
        X_img, Y_img = self.hparams.img_domain_x, self.hparams.img_domain_y
        test_csv = "/cluster/home/carrowsm/ArtifactNet/datasets/test_labels.csv"

        x_df_trg, x_df_val, y_df_trg, y_df_val = load_image_data_frame(
                                            self.hparams.csv_path, X_img, Y_img,
                                            val_split=0.1) # Use 10% of data for val
        x_df_test, _, y_df_test, _ = load_image_data_frame(test_csv,
                                                           X_img, Y_img,
                                                           val_split=0)
        # Define sequence of transforms
        trg_transform = torchvision.transforms.Compose([
                    HorizontalFlip(),
                    AffineTransform(max_angle=30.0, max_pixels=[20, 20]),
                    Normalize(-1000.0, 1000.0),
                    ToTensor()])
        val_transform = torchvision.transforms.Compose([
                                                Normalize(-1000.0, 1000.0),
                                                ToTensor()])
        test_transform = val_transform

        # Train data loader
        trg_dataset = UnpairedDataset(x_df_trg, y_df_trg,
                                      image_dir=self.hparams.img_dir,
                                      cache_dir=os.path.join(self.hparams.cache_dir,
                                                             "unpaired"),
                                      file_type="DICOM",
                                      image_size=self.image_size,
                                      image_spacing=[2.0, 1.0, 1.0],
                                      dim=self.dimension,
                                      transform=trg_transform,
                                      num_workers=self.hparams.n_cpus)
        val_dataset = UnpairedDataset(x_df_val, y_df_val,
                                      image_dir=self.hparams.img_dir,
                                      cache_dir=os.path.join(self.hparams.cache_dir,
                                                             "unpaired"),
                                      file_type="DICOM",
                                      image_size=self.image_size,
                                      image_spacing=[2.0, 1.0, 1.0],
                                      dim=self.dimension,
                                      transform=val_transform,
                                      num_workers=self.hparams.n_cpus)
        test_dataset = UnpairedDataset(x_df_test, y_df_test,
                                      image_dir=self.hparams.img_dir,
                                      cache_dir=os.path.join(self.hparams.cache_dir,
                                                             "unpaired"),
                                      file_type="DICOM",
                                      image_size=self.image_size,
                                      image_spacing=[2.0, 1.0, 1.0],
                                      dim=self.dimension,
                                      transform=test_transform,
                                      num_workers=self.hparams.n_cpus)

        self.trg_dataset = trg_dataset
        self.val_dataset = val_dataset
        self.test_dataset = test_dataset

        # If caching data for the first time, save the image centre coordinates
        if trg_dataset.first_cache and val_dataset.first_cache :
            df = pd.concat([trg_dataset.full_df, val_dataset.full_df])
            df.to_csv(self.hparams.csv_path)
        if test_dataset.first_cache :
            test_dataset.full_df.to_csv(test_csv)



    def on_train_start(self):
        """ Print some info before training starts """
        print("\nDataset sizes")
        print("=============")
        print(f"Training:   {len(self.trg_dataset)}")
        print(f"Validation: {len(self.val_dataset)}")


    @pl.data_loader
    def train_dataloader(self):
        data_loader = DataLoader(self.trg_dataset,
                                 batch_size=self.hparams.batch_size,
                                 shuffle=True,
                                 num_workers=self.hparams.n_cpus - 1,
                                 drop_last=True,
                                 pin_memory=True)
        self.dataset_size = len(self.trg_dataset)
        return data_loader



    @pl.data_loader
    def val_dataloader(self) :
        data_loader = DataLoader(self.val_dataset,
                                 batch_size=self.hparams.batch_size,
                                 shuffle=False,
                                 num_workers=self.hparams.n_cpus - 1,
                                 drop_last=True,
                                 pin_memory=True)
        return data_loader


    """ Helper functions for forward pass """
    def forward(self, real_X, real_Y):
        # Perform a forward pass on the correct network
        """Run forward pass"""
        fake_Y = self.g_y(real_X)               # G_Y(X)
        cycl_X = self.g_x(fake_Y)               # G_X(G_Y(X))
        fake_X = self.g_x(real_Y)               # G_X(Y)
        cycl_Y = self.g_y(fake_X)               # G_Y(G_X(Y))
        return fake_Y, cycl_X, fake_X, cycl_Y



    def training_step(self, batch, batch_nb, optimizer_idx) :
        self.zero_grad()       # Zero the gradients to prevent gradient accumulation

        # Get the images
        x, y = batch           # x == DA+ images, y == DA- images

        batch_size = x.size(0)

        # Create ground truth results
        zeros = torch.zeros(batch_size, device=x.device)
        ones  = torch.ones(batch_size, device=x.device)

        ### TRAIN GENERATORS ###
        if optimizer_idx == 0 :
            ### Calculate G_Y adversarial loss ###
            # Generate fake DA- images from real DA+ imgs
            gen_y = self.g_y(x)

            # Run discriminator on generated images
            d_y_gen_y = self.d_y(gen_y).view(-1)

            # Compute adversarial loss
            loss_adv_Y = self.adv_loss(d_y_gen_y, ones)

            ### Calculate G_X adversarial loss ###
            # Generate fake DA- images from real DA+ imgs
            gen_x = self.g_x(y)

            # Run discriminator on generated images
            d_x_gen_x = self.d_x(gen_x).view(-1)

            # Compute adversarial loss
            loss_adv_X = self.adv_loss(d_x_gen_x, ones)

            ### Compute Cycle Consistency Loss ###
            # Generate fake images from fake images (for cyclical loss)
            gen_x_gen_y = self.g_x(gen_y)     # fake DA+ from fake DA-
            gen_y_gen_x = self.g_y(gen_x)     # fake DA- from fake DA+

            # Compute cyclic loss
            loss_cyc_X = self.l1_loss(gen_x_gen_y, x) * self.lam
            loss_cyc_Y = self.l1_loss(gen_y_gen_x, y) * self.lam

            ### Compute Identidy loss ###
            loss_idt = (self.l1_loss(gen_y, x) + self.l1_loss(gen_x, y)) * self.idt

            # Generator loss is the sum of these
            G_loss = loss_adv_Y + loss_adv_X + loss_cyc_X + loss_cyc_Y + loss_idt

            g_loss_dict = {'G_total': G_loss,
                           "G_idt":   loss_idt,
                           "G_adv_Y": loss_adv_Y,
                           "G_adv_X": loss_adv_X,
                           "G_cyc_Y": loss_cyc_Y,
                           "G_cyc_X": loss_cyc_X}

            # Save the discriminator loss in a dictionary
            tqdm_dict = {'g_loss': G_loss}
            output = OrderedDict({'loss': G_loss,              # A parameter for PT-lightning
                                  'progress_bar': tqdm_dict,   # Will appear in progress bar
                                  'log': g_loss_dict           # Will be plotted on tensorboard
                                  })

        ### TRAIN DISCRIMINATORS ###
        if optimizer_idx == 1 :
            # Generate some images
            gen_y = self.g_y(x) # fake DA- images from real DA+ imgs
            gen_x = self.g_x(y) # fake DA+ images from real DA- imgs

            # Forward pass through each discriminator
            # Run discriminator on generated images
            d_y_fake = self.d_y(gen_y.detach()).view(-1)
            d_x_fake = self.d_x(gen_x.detach()).view(-1)
            d_y_real = self.d_y(y).view(-1)
            d_x_real = self.d_x(x).view(-1)

            # Compute loss for each discriminator
            loss_Dy = self.adv_loss(d_y_fake, zeros) + self.adv_loss(d_y_real, ones)
            loss_Dx = self.adv_loss(d_x_fake, zeros) + self.adv_loss(d_x_real, ones)
            D_loss = (loss_Dy + loss_Dx) / 2
            # ----------------------- #

            # Save the discriminator loss in a dictionary
            tqdm_dict = {'d_loss': D_loss}
            output = OrderedDict({'loss': D_loss,
                                  'progress_bar': tqdm_dict,
                                  'log': tqdm_dict
                                  })
        ### ---------------------- ###
        return output


    def validation_step(self, batch, batch_idx):
        print("Starting val step")
        # Get the images
        x, y = batch           # x == DA+ images, y == DA- images

        batch_size = x.size(0)

        # Create ground truth results
        zeros = torch.zeros(batch_size, device=x.device)
        ones = torch.ones(batch_size, device=x.device)

        gen_y = self.g_y(x)             # Generate DA- images from original DA+
        gen_x = self.g_x(y)             # Generate DA+ images from original DA-
        gen_x_gen_y = self.g_x(gen_y)   # Generate DA+ images from fake DA-
        gen_y_gen_x = self.g_y(gen_x)   # Generate DA- images from fake DA+

        ### DISCRIMINATOR LOSS ###
        # Forward pass through each discriminator
        d_y_fake = self.d_y(gen_y).view(-1)
        d_x_fake = self.d_x(gen_x).view(-1)
        d_y_real = self.d_y(y).view(-1)
        d_x_real = self.d_x(x).view(-1)

        # Compute loss for each discriminator
        loss_Dy = self.adv_loss(d_y_fake, zeros) + self.adv_loss(d_y_real, ones)
        loss_Dx = self.adv_loss(d_x_fake, zeros) + self.adv_loss(d_x_real, ones)
        D_loss = (loss_Dy + loss_Dx)
        ### ------------------ ###

        ### -- GENERATOR LOSS -- ###
        # Compute adversarial loss
        loss_adv_X = self.adv_loss(d_x_fake, ones)
        loss_adv_Y = self.adv_loss(d_y_fake, ones)

        # Compute Cycle Consistency Loss
        loss_cyc_X = self.l1_loss(gen_x_gen_y, x) * self.lam
        loss_cyc_Y = self.l1_loss(gen_y_gen_x, y) * self.lam

        # Compute Identidy loss
        loss_idt = (self.l1_loss(gen_y, x) + self.l1_loss(gen_x, y)) * self.idt

        # Generator loss is the sum of these
        G_loss = loss_adv_Y + loss_adv_X + loss_cyc_X + loss_cyc_Y + loss_idt
        ### -- ------------- -- ###

        # Save the losses in a dictionary
        output = OrderedDict({'d_loss_val': D_loss, 'g_loss_val': G_loss})

        # Compute 'accuracy'
        # output["acc"] = self.l1_loss(gen_y, y)# Comment out if val set is unpaired

        ### Log some sample images once per val_step ###
        if batch_idx == 0 :
            if self.dimension == 2 :
                images = [    x[0, self.image_size[0] // 2, :, :].cpu(),
                          gen_y[0, self.image_size[0] // 2, :, :].cpu(),
                              y[0, self.image_size[0] // 2, :, :].cpu(),
                          gen_x[0, self.image_size[0] // 2, :, :].cpu()]
            else :
                images = [    x[0, 0, self.image_size[0] // 2, :, :].cpu(),
                          gen_y[0, 0, self.image_size[0] // 2, :, :].cpu(),
                              y[0, 0, self.image_size[0] // 2, :, :].cpu(),
                          gen_x[0, 0, self.image_size[0] // 2, :, :].cpu()]
            # Plot the image
            self.logger.add_mpl_img(f'imgs',
                                    images,
                                    self.current_epoch,
                                    clip_vals=True)

        return output


    def validation_epoch_end(self, outputs):
        g_loss_mean, d_loss_mean, overall_loss = 0, 0, 0
        val_size = len(outputs)

        for output in outputs: # Average over all val batches
            d_loss_mean += output['d_loss_val'] / val_size
            g_loss_mean += output['g_loss_val'] / val_size
            overall_loss += output['d_loss_val'] / val_size +\
                           output['g_loss_val'] / val_size


        tqdm_dict = {'d_loss_val': d_loss_mean.detach(),
                     'g_loss_val': g_loss_mean.detach(),
                     "val_loss": overall_loss.detach()
                     }

        # show val_acc in progress bar but only log val_loss
        results = {'progress_bar': tqdm_dict,
                   'log': tqdm_dict}

        return results


    def configure_optimizers(self):
        """
        Define two optimizers (D & G), each with its own learning rate scheduler.
        """
        lr = self.hparams.lr
        b1, b2 = self.hparams.b1, self.hparams.b2

        opt_g = torch.optim.Adam(itertools.chain(self.g_x.parameters(),
                                 self.g_y.parameters()), lr=lr, betas=(b1, b2),
                                 weight_decay=self.hparams.weight_decay)
        opt_d = torch.optim.Adam(itertools.chain(self.d_x.parameters(),
                                 self.d_y.parameters()), lr=lr, betas=(b1, b2),
                                 weight_decay=self.hparams.weight_decay)

        scheduler_g = {# Scheduler will reduce lr if loss plateaus for 7 epochs
                       'scheduler': ReduceLROnPlateau(opt_g, 'min', patience=7,
                                                      verbose=True, factor=0.5),
                       'monitor': 'g_loss_val', # Default: val_loss
                       'interval': 'epoch',
                       'frequency': 1}
        scheduler_d = {'scheduler': ReduceLROnPlateau(opt_d, 'min', patience=7,
                                                      verbose=True, factor=0.5),
                       'monitor': 'd_loss_val', # Default: val_loss
                       'interval': 'epoch',
                       'frequency': 1}
        return [opt_g, opt_d], [scheduler_g, scheduler_d]



def main(hparams):
    # ------------------------
    # 1 INIT LIGHTNING MODEL (possibly from checkpoint)
    # ------------------------
    print("------------------------------")
    if hparams.checkpoint == "None" or hparams.checkpoint is None :
        print("Starting training from scratch")
        model = GAN(hparams=hparams)
    else :
        print("Starting training from checkpoint: ", hparams.checkpoint)
        model = GAN.load_from_checkpoint(hparams.checkpoint, hparams=hparams)
    print("------------------------------")

    # ------------------------
    # 2 INIT LOGGER -  Custom logger defined in loggers.py
    # ------------------------
    log_name = str(hparams.image_size)[1 : -1].replace(", ", "_") +\
                    "px/"+ "u".join(hparams.img_domain_x) + "-" +\
                    "u".join(hparams.img_domain_y)
    print(log_name)
    logger = TensorBoardCustom(hparams.log_dir, name=log_name)

    # ------------------------
    # 3 INIT CHECKPOINTING
    # ------------------------
    checkpoint_path = os.path.join(logger.experiment.get_logdir(),
                                   "checkpoints",
                                   "{epoch:02d}")
    checkpoint_callback = ModelCheckpoint(filepath=checkpoint_path,
                                          # save_last=True,
                                          save_top_k=1,
                                          monitor="val_loss",
                                          mode="min")

    # Main PLT training module
    trainer = pl.Trainer(logger=logger,
                         # accumulate_grad_batches=10,
                         # gradient_clip_val=0.1,
                         # val_percent_check=1,
                         amp_level='O1', precision=16, # Enable 16-bit presicion
                         gpus=hparams.n_gpus,
                         num_nodes=1,
                         distributed_backend="ddp",
                         benchmark=True,
                         # val_check_interval=0.2    # Check validation 5 times per epoch
                         )

    # ------------------------
    # 4 START TRAINING /
    # ------------------------
    trainer.fit(model)

if __name__ == '__main__':

    # Get Hyperparameters and other arguments for training
    hparams, unparsed_args = get_args()



    main(hparams)
