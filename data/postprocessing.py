import io
import os
import numpy as np
import torch
from typing import Callable, Optional, Union, Sequence

import SimpleITK as sitk
from skimage.transform import resize

from preprocessing import resample_image, get_dicom_path, read_dicom_image


def insert_volume(subvolume: sitk.Image, full_image: sitk.Image) -> sitk.Image :
    """ Insert a subvolume into the original image, replacing those pixels.
    """
    spacing = full_image.GetSpacing()
    origin  = full_image.GetOrigin()
    subvol_size = subvolume.GetSpacing()

    # Resample subvolume to match original image
    subvolume = resample_image(subvolume, spacing)


    return


def process_one_image(gen_img: torch.Tensor, patient_id: str) :
    """
    """
    # Read patient's full DICOM image

class PostProcessor :
    """PostProcessor class to take the ouput tensor from a model and save it as
    a full-size NRRD or DICOM.
    """
    def __init__(self,
                 input_dir: str,
                 output_dir: str,
                 output_spacing: Sequence = [3, 1, 1],
                 output_file_type: str = "nrrd"):
        """ Initialize the class
        Parameters
        ----------
        input_dir (str)
            The directory containing the original DICOM images. Each patient's
            DICOM series should be contained in  a subdirectory named with the
            patient's ID.
        output_dir (str)
            The directory in which to save the generated images.
        output_spacing (Callable)
            The spacing of the output SITK file. Expected to be [z, y, x].
        output_file_type (str)
            The file type to save the output files. Either 'nrrd' or 'dicom'.
        """
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.output_spacing = output_spacing
        self.output_file_type = output_file_type

        # Get the correct function to with which to save images
        if self.output_file_type == 'nrrd' :
            self.save_outout_img = sitk.WriteImage
        elif self.output_file_type == 'dicom' :
            raise NotImplementedError(
            "Saving output as DICOM is not currently supported. Please use 'nrrd'.")
        else :
            raise NotImplementedError("output_file_type must be 'dicom' or 'nrrd'.")


    def __call__(self, model_output: torch.Tensor, patient_id: str, img_centre: Sequence) :
        """ Process a single image from the output of a deep learning generator.
        Convert the image to an SITK image, combine it with the original
        full-size DICOM, and save the image.

        Parameters
        ----------
        model_output (torch.Tensor)
            The direct output from the generator model. This will be used to
            replace pixels in the original image.
        patient_id (str)
            The patient ID or MRN of the current patient. This will be used to
            access the original DICOM and save the output.
        center_xyz (Sequence)
            The location of the centre of the subvolume in physical coordinates.
        """
        # Convert the tensor image to SITK (with 1mm voxel spacing)
        sub_img = sitk.GetImageFromArray(model_output.numpy())

        # Load DICOM image
        dicom_path = get_dicom_path(os.path.join(self.input_dir, patient_id))
        full_img = read_dicom_image(dicom_path)

        # Resample subvolume image to the same spacing as full image
        full_img_spacing = full_img.GetSpacing()
        sub_img = resample_image(sub_img, full_img_spacing)
        sub_img_size = sub_img.GetSize()

        # Get the coordinates of the
