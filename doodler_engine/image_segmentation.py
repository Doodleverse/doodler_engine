# Written by Dr Daniel Buscombe, Marda Science LLC
# for the USGS Coastal Change Hazards Program
#
# MIT License
#
# Copyright (c) 2020-2022, Marda Science LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

#========================================================
## ``````````````````````````` imports
##========================================================

#numerical
import numpy as np
np.seterr(divide='ignore', invalid='ignore')

#classifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from scipy.signal import convolve2d

#crf
import pydensecrf.densecrf as dcrf
from pydensecrf.utils import create_pairwise_bilateral, unary_from_softmax, unary_from_labels

#utility
from tempfile import TemporaryFile
from joblib import Parallel, delayed
import io, os, logging, psutil, itertools
# from datetime import datetime
from skimage import filters, feature, img_as_float32
from skimage.transform import resize

##========================================================
def fromhex(n):
    """ hexadecimal to integer """
    return int(n, base=16)

##========================================================
def rescale(dat,
    mn,
    mx):
    '''
    rescales an input dat between mn and mx
    '''
    m = min(dat.flatten())
    M = max(dat.flatten())
    return (mx-mn)*(dat-m)/(M-m)+mn

##====================================
def standardize(img):
    '''
    standardize a 3 band image using adjusted standard deviation
    (1-band images are standardized and returned as 3-band images)
    '''
    #
    N = np.shape(img)[0] * np.shape(img)[1]
    s = np.maximum(np.std(img), 1.0/np.sqrt(N))
    m = np.mean(img)
    img = (img - m) / s
    img = rescale(img, 0, 1)
    del m, s, N

    if np.ndim(img)!=3:
        img = np.dstack((img,img,img))

    return img

# ##========================================================
def inpaint_nans(im):
    '''
    quick and dirty nan inpainting using kernel trick
    '''
    ipn_kernel = np.array([[1,1,1],[1,0,1],[1,1,1]]) # kernel for inpaint_nans
    nans = np.isnan(im)
    while np.sum(nans)>0:
        im[nans] = 0
        vNeighbors = convolve2d((nans==False),ipn_kernel,mode='same',boundary='symm')
        im2 = convolve2d(im,ipn_kernel,mode='same',boundary='symm')
        im2[vNeighbors>0] = im2[vNeighbors>0]/vNeighbors[vNeighbors>0]
        im2[vNeighbors==0] = np.nan
        im2[(nans==False)] = im[(nans==False)]
        im = im2
        nans = np.isnan(im)
    return im


##========================================================
def crf_refine_from_integer_labels(label,
    img,n,
    crf_theta_slider_value,
    crf_mu_slider_value,
    crf_downsample_factor): #gt_prob
    """
    "crf_refine(label, img)"
    This function refines a label image based on an input label image and the associated image
    Uses a conditional random field algorithm using spatial and image features
    INPUTS:
        * label [ndarray]: label image 2D matrix of integers
        * image [ndarray]: image 3D matrix of integers
    OPTIONAL INPUTS: None
    GLOBAL INPUTS: None
    OUTPUTS: label [ndarray]: label image 2D matrix of integers
    """

    Horig = img.shape[0]
    Worig = img.shape[1]
    l_unique = len(np.unique(label)) #label.shape[-1]

    # label = label.reshape(Horig,Worig,l_unique)

    scale = 1+(5 * (np.array(img.shape).max() / 3000))
    logging.info('CRF scale: %f' % (scale))

    logging.info('CRF downsample factor: %f' % (crf_downsample_factor))
    logging.info('CRF theta parameter: %f' % (crf_theta_slider_value))
    logging.info('CRF mu parameter: %f' % (crf_mu_slider_value))

    # decimate by factor by taking only every other row and column
    img = img[::crf_downsample_factor,::crf_downsample_factor, :]
    # do the same for the label image
    label = label[::crf_downsample_factor,::crf_downsample_factor]
    # yes, I know this aliases, but considering the task, it is ok; the objective is to
    # make fast inference and resize the output

    logging.info('Images downsampled by a factor of %f' % (crf_downsample_factor))

    H = img.shape[0]
    W = img.shape[1]
    U = unary_from_labels(label.astype('int'), n, gt_prob=0.51) #np.argmax(label,-1)
    d = dcrf.DenseCRF2D(H, W, n)

    # U = unary_from_softmax(np.ascontiguousarray(np.rollaxis(label,-1,0)))
    # d = dcrf.DenseCRF2D(H, W, l_unique)

    d.setUnaryEnergy(U)

    # to add the color-independent term, where features are the locations only:
    d.addPairwiseGaussian(sxy=(3, 3),
                 compat=3,
                 kernel=dcrf.DIAG_KERNEL,
                 normalization=dcrf.NORMALIZE_SYMMETRIC)
    feats = create_pairwise_bilateral(
                          sdims=(crf_theta_slider_value, crf_theta_slider_value),
                          schan=(scale,scale,scale),
                          img=img,
                          chdim=2)

    d.addPairwiseEnergy(feats, compat=crf_mu_slider_value, kernel=dcrf.DIAG_KERNEL,normalization=dcrf.NORMALIZE_SYMMETRIC) #260

    logging.info('CRF feature extraction complete ... inference starting')

    Q = d.inference(10)
    result = np.argmax(Q, axis=0).reshape((H, W)).astype(np.uint8) +1
    logging.info('CRF inference made')

    # uniq = np.unique(result.flatten())

    result = resize(result, (Horig, Worig), order=0, anti_aliasing=False) #True)
    result = rescale(result, 1, l_unique).astype(np.uint8)

    # result = rescale(result, orig_mn, orig_mx).astype(np.uint8)

    logging.info('label resized and rescaled ... CRF from labels post-processing complete')

    return result, l_unique

##========================================================
def crf_refine(label,
    img,n,
    crf_theta_slider_value,
    crf_mu_slider_value,
    crf_downsample_factor): #gt_prob
    """
    "crf_refine(label, img)"
    This function refines a label image based on an input label image and the associated image
    Uses a conditional random field algorithm using spatial and image features
    INPUTS:
        * label [ndarray]: label image 2D matrix of integers
        * image [ndarray]: image 3D matrix of integers
    OPTIONAL INPUTS: None
    GLOBAL INPUTS: None
    OUTPUTS: label [ndarray]: label image 2D matrix of integers
    """

    Horig = img.shape[0]
    Worig = img.shape[1]
    l_unique = label.shape[-1]

    label = label.reshape(Horig,Worig,l_unique)

    scale = 1+(5 * (np.array(img.shape).max() / 3000))
    logging.info('CRF scale: %f' % (scale))

    logging.info('CRF downsample factor: %f' % (crf_downsample_factor))
    logging.info('CRF theta parameter: %f' % (crf_theta_slider_value))
    logging.info('CRF mu parameter: %f' % (crf_mu_slider_value))

    # decimate by factor by taking only every other row and column
    img = img[::crf_downsample_factor,::crf_downsample_factor, :]
    # do the same for the label image
    label = label[::crf_downsample_factor,::crf_downsample_factor]
    # yes, I know this aliases, but considering the task, it is ok; the objective is to
    # make fast inference and resize the output

    logging.info('Images downsampled by a factor of %f' % (crf_downsample_factor))

    H = img.shape[0]
    W = img.shape[1]
    # U = unary_from_labels(np.argmax(label,-1).astype('int'), n, gt_prob=gt_prob)
    # d = dcrf.DenseCRF2D(H, W, n)

    U = unary_from_softmax(np.ascontiguousarray(np.rollaxis(label,-1,0)))
    d = dcrf.DenseCRF2D(H, W, l_unique)

    d.setUnaryEnergy(U)

    # to add the color-independent term, where features are the locations only:
    d.addPairwiseGaussian(sxy=(3, 3),
                 compat=3,
                 kernel=dcrf.DIAG_KERNEL,
                 normalization=dcrf.NORMALIZE_SYMMETRIC)
    feats = create_pairwise_bilateral(
                          sdims=(crf_theta_slider_value, crf_theta_slider_value),
                          schan=(scale,scale,scale),
                          img=img,
                          chdim=2)

    d.addPairwiseEnergy(feats, compat=crf_mu_slider_value, kernel=dcrf.DIAG_KERNEL,normalization=dcrf.NORMALIZE_SYMMETRIC) #260

    logging.info('CRF feature extraction complete ... inference starting')

    Q = d.inference(10)
    result = np.argmax(Q, axis=0).reshape((H, W)).astype(np.uint8) +1
    logging.info('CRF inference made')

    # uniq = np.unique(result.flatten())

    result = resize(result, (Horig, Worig), order=0, anti_aliasing=False) #True)
    result = rescale(result, 1, l_unique).astype(np.uint8)

    # result = rescale(result, orig_mn, orig_mx).astype(np.uint8)

    logging.info('label resized and rescaled ... CRF from softmax post-processing complete')

    return result, l_unique

##========================================================
def features_sigma(img,
    sigma,
    intensity=True,
    edges=True,
    texture=True):
    """Features for a single value of the Gaussian blurring parameter ``sigma``
    """

    features = []

    gx,gy = np.meshgrid(np.arange(img.shape[1]), np.arange(img.shape[0]))
    gx = filters.gaussian(gx, sigma)
    gy = filters.gaussian(gy, sigma)

    features.append(np.sqrt(gx**2 + gy**2)) #use polar radius of pixel locations as cartesian coordinates

    del gx, gy

    logging.info('Location features extracted using sigma= %f' % (sigma))

    img_blur = filters.gaussian(img, sigma)

    if intensity:
        features.append(img_blur)

    logging.info('Intensity features extracted using sigma= %f' % (sigma))

    if edges:
        features.append(filters.sobel(img_blur))

    logging.info('Edge features extracted using sigma= %f' % (sigma))

    if texture:
        H_elems = [
            np.gradient(np.gradient(img_blur)[ax0], axis=ax1)
            for ax0, ax1 in itertools.combinations_with_replacement(range(img.ndim), 2)
        ]

        eigvals = feature.hessian_matrix_eigvals(H_elems)
        del H_elems

        for eigval_mat in eigvals:
            features.append(eigval_mat)
        del eigval_mat

    logging.info('Texture features extracted using sigma= %f' % (sigma))
    logging.info('Image features extracted using sigma= %f' % (sigma))

    return features

##========================================================
def extract_features_2d(
    dim,
    img,
    n_sigmas,
    intensity=True,
    edges=True,
    texture=True,
    sigma_min=0.5,
    sigma_max=16
):
    """Features for a single channel image. ``img`` can be 2d or 3d.
    """
    logging.info('Extracting features from channel %i' % (dim))

    # computations are faster as float32
    img = img_as_float32(img)

    sigmas = np.logspace(
        np.log2(sigma_min),
        np.log2(sigma_max),
        num=n_sigmas, 
        base=2,
        endpoint=True,
    )

    if (psutil.virtual_memory()[0]>10000000000) & (psutil.virtual_memory()[2]<50): #>10GB and <50% utilization
        logging.info('Extracting features in parallel')
        logging.info('Total RAM: %i' % (psutil.virtual_memory()[0]))
        logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

        all_results = Parallel(n_jobs=-2, verbose=0)(delayed(features_sigma)(img, sigma, intensity=intensity, edges=edges, texture=texture) for sigma in sigmas)
    else:

        logging.info('Extracting features in series')
        logging.info('Total RAM: %i' % (psutil.virtual_memory()[0]))
        logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

        n_sigmas = len(sigmas)
        all_results = [
            features_sigma(img, sigma, intensity=intensity, edges=edges, texture=texture)
            for sigma in sigmas
        ]

    logging.info('Features from channel %i for all scales' % (dim))

    return list(itertools.chain.from_iterable(all_results))

##========================================================
def extract_features(
    img,
    n_sigmas,
    multichannel=True,
    intensity=True,
    edges=True,
    texture=True,
    sigma_min=0.5,
    sigma_max=16,
):
    """Features for a single- or multi-channel image.
    """
    if multichannel: 
        all_results = (
            extract_features_2d(
                dim,
                img[..., dim],
                n_sigmas,
                intensity=intensity,
                edges=edges,
                texture=texture,
                sigma_min=sigma_min,
                sigma_max=sigma_max,
            )
            for dim in range(img.shape[-1])
        )
        features = list(itertools.chain.from_iterable(all_results))
    else:
        features = extract_features_2d(0,
            img,
            n_sigmas,
            intensity=intensity,
            edges=edges,
            texture=texture,
            sigma_min=sigma_min,
            sigma_max=sigma_max,
        )

    logging.info('Feature extraction complete')

    logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))
    logging.info('Memory mapping features to temporary file')

    features = memmap_feats(features)
    logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

    return features #np.array(features)

##========================================================
def memmap_feats(features):
    """
    Memory-map data to a temporary file
    """
    features = np.array(features)
    dtype = features.dtype
    feats_shape = features.shape

    outfile = TemporaryFile()
    fp = np.memmap(outfile, dtype=dtype, mode='w+', shape=feats_shape)
    fp[:] = features[:]
    fp.flush()
    del features
    del fp
    logging.info('Features memory mapped features to temporary file: %s' % outfile)

    #read back in again without using any memory
    features = np.memmap(outfile, dtype=dtype, mode='r', shape=feats_shape)
    return features

##========================================================
def do_classify(img,mask,n_sigmas,multichannel,intensity,edges,texture,sigma_min,sigma_max, downsample_value):
    """
    Apply classifier to features to extract unary potentials for the CRF
    """

    # print('MLP ...')
    logging.info('Extracting features for MLP classifier')

    if np.ndim(img)==3:
        features = extract_features(
            img,
            n_sigmas,
            multichannel=multichannel,
            intensity=intensity,
            edges=edges,
            texture=texture,
            sigma_min=sigma_min,
            sigma_max=sigma_max,
        )
    else:
        features = extract_features(
            np.dstack((img,img,img)),
            n_sigmas,
            multichannel=multichannel,
            intensity=intensity,
            edges=edges,
            texture=texture,
            sigma_min=sigma_min,
            sigma_max=sigma_max,
        )

    logging.info('features extracted for MLP classifier')

    if mask is None:
        raise ValueError("If no classifier clf is passed, you must specify a mask.")
    training_data = features[:, mask > 0].T

    training_data = memmap_feats(training_data)

    training_labels = mask[mask > 0].ravel()

    training_data = training_data[::downsample_value]
    training_labels = training_labels[::downsample_value]

    unique_labels = np.unique(training_labels)

    lim_samples = 100000 #200000

    if training_data.shape[0]>lim_samples:
        logging.info('Number of samples exceeds %i'% lim_samples)
        ind = np.round(np.linspace(0,training_data.shape[0]-1,lim_samples)).astype('int')
        training_data = training_data[ind,:]
        training_labels = training_labels[ind]
        logging.info('Samples have been subsampled')
        logging.info('Number of samples in training data: %i' % (training_data.shape[0]))
        print(training_data.shape)

    clf = make_pipeline(
            StandardScaler(),
            MLPClassifier(
                solver='adam', alpha=1, random_state=1, max_iter=2000,
                early_stopping=True, hidden_layer_sizes=[100, 60],
            ))
    logging.info('Initializing MLP model')

    clf.fit(training_data, training_labels)
    logging.info('MLP model fit to data')

    del training_data, training_labels

    # use model in predictive mode
    sh = features.shape
    features_use = features.reshape((sh[0], np.prod(sh[1:]))).T

    sh = features_use.shape

    result = clf.predict_proba(features_use)

    result = result.reshape((sh[0],)+(len(unique_labels),))

    sh = result.shape

    result2 = result.copy()
    del result

    # logging.info(datetime.now().strftime("%Y-%m-%d-%H-%M-%S"))
    logging.info('RF feature extraction and model fitting complete')
    logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

    return result2, unique_labels

    # gt_prob,

# ##========================================================
def segmentation(
    img, mask,
    crf_theta_slider_value,
    crf_mu_slider_value,
    rf_downsample_value,
    crf_downsample_factor,
    n_sigmas,
    multichannel,#=True,
    intensity,#=True,
    edges,#=True,
    texture,#=True,
    sigma_min,#=0.5,
    sigma_max,#=16,
):
    """
    1) Calls do_classify to apply classifier to features to extract unary potentials for the CRF
    then
    2) Calls the spatial filter
    Then
    3) Calls crf_refine to apply CRF
    """

    # #standardization using adjusted standard deviation
    img = standardize(img)

    logging.info('Image standardized')

    for ni in np.unique(mask[1:]):
        logging.info('examples provided of %i' % (ni))

    if len(np.unique(mask)[1:])==1:

        logging.info('Only one class annotation provided, skipping MLP and CRF and coding all pixels %i' % (np.unique(mask)[1:]))
        crf_result = np.ones(mask.shape[:2])*np.unique(mask)[1:]
        crf_result = crf_result.astype(np.uint8)
        logging.info('label creation complete')

    else:

        #================================
        # MLP analysis
        n=len(np.unique(mask)[1:])

        mlp_result, unique_labels = do_classify(img,mask,n, #n_sigmas,
                                                multichannel,intensity,edges,
                                                texture, sigma_min,sigma_max, rf_downsample_value)

        logging.info('MLP model applied with sigma range %f : %f' % (sigma_min,sigma_max))
        logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

        mlp_result = mlp_result.reshape(img.shape[0],img.shape[1],len(unique_labels))

        mlp_result = np.argmax(mlp_result,-1)+1

        uniq_doodles = np.unique(mask)[1:]
        uniq_mlp = np.unique(mlp_result)
        mlp_result2 = np.zeros_like(mlp_result)
        for o,e in zip(uniq_doodles,uniq_mlp):
            mlp_result2[mlp_result==e] = o

        mlp_result = mlp_result2.copy()-1
        # print(np.unique(mlp_result))
        logging.info('MLP result recoded to set of classes present in the doodles')

        # make a limited one-hot array and add the available bands
        nx, ny = mlp_result.shape
        mlp_result_softmax = np.zeros((nx,ny,n))
        mlp_result_softmax[:,:,:n] = (np.arange(n) == 1+mlp_result[...,None]-1).astype(int)

        # if not np.all(uniq_doodles-1==np.unique(np.argmax(mlp_result_softmax,-1))):
        if not n==len(np.unique(np.argmax(mlp_result_softmax,-1))):
            logging.info('MLP method failed')

            try:
                logging.info('CRF from original doodles being computed')                
                crf_result, n = crf_refine_from_integer_labels(mask, img, n,
                                                                crf_theta_slider_value, crf_mu_slider_value, 
                                                                crf_downsample_factor)

                logging.info('CRF model applied with theta=%f and mu=%f' % ( crf_theta_slider_value, crf_mu_slider_value))
                logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

                uniq_crf = np.unique(crf_result)
                crf_result2 = np.zeros_like(crf_result)
                for o,e in zip(uniq_doodles,uniq_crf):
                    crf_result2[crf_result==e] = o
                logging.info('CRF result recoded to set of classes present in the doodles')

                crf_result = crf_result2.copy()-1

            except:
                crf_result = mlp_result.copy()
        else:
            #================================
            # CRF analysis
            # print('CRF ...')
            try:
                logging.info('CRF from MLP softmax scores being computed')                
                crf_result, _ = crf_refine(mlp_result_softmax, img, n,
                                        crf_theta_slider_value, crf_mu_slider_value,
                                        crf_downsample_factor)

                uniq_crf = np.unique(crf_result)
                crf_result2 = np.zeros_like(crf_result)
                for o,e in zip(uniq_doodles,uniq_crf):
                    crf_result2[crf_result==e] = o

                crf_result = crf_result2.copy()-1
                logging.info('CRF result recoded to set of classes present in the doodles')

                # if not np.all(uniq_doodles-1==np.unique(crf_result)):
                if not len(uniq_doodles)==len(np.unique(crf_result)):

                    # print("CRF failed")
                    logging.info('CRF from MLP softmax scores failed')
                    logging.info('CRF from original integer doodles being computed')

                    crf_result, _ = crf_refine_from_integer_labels(mask, img, n,
                                                                    crf_theta_slider_value, crf_mu_slider_value, 
                                                                    crf_downsample_factor)

                    logging.info('CRF model applied with theta=%f and mu=%f' % ( crf_theta_slider_value, crf_mu_slider_value))
                    logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

                    uniq_crf = np.unique(crf_result)
                    crf_result2 = np.zeros_like(crf_result)
                    for o,e in zip(uniq_doodles,uniq_crf):
                        crf_result2[crf_result==e] = o

                    crf_result = crf_result2.copy()-1
                    logging.info('CRF result recoded to set of classes present in the doodles')

            except:
                crf_result = mlp_result.copy()


    return crf_result

            # result2, n = crf_refine(result, img, n,
            #                         crf_theta_slider_value, crf_mu_slider_value, 
            #                         crf_downsample_factor)#, gt_prob)

            # match2 = np.unique(result2-1)
            # # print(match2)
            # if not np.all(np.array(match)==np.array(match2)):
            #     print("MLP and CRF solutions are unmatched in terms of number of classes.... ")
            #     print("Bypassing MLP and using doodles directly with a modified CRF .... ")

            #     logging.info('MLP and CRF solutions are unmatched in terms of number of classes')
            #     logging.info('Bypassing MLP and using doodles directly with a modified CRF')

            #     result2, n = crf_refine_from_integer_labels(mask, img, n,
            #                                                 crf_theta_slider_value, crf_mu_slider_value, 
            #                                                 crf_downsample_factor)

        # # set to zero any labels not present in the original labels
        # for k in np.setdiff1d(np.unique(np.argmax(result),-1), unique_labels):
        #     result2[result2==k]=0

        # print(np.unique(crf_result))

        # logging.info('Weighted average applied to test-time augmented outputs')

        # if ((n==1)):
        #     crf_result[mlp_result>0] = np.unique(mlp_result)

        # crf_result = crf_result.astype('float')
        # crf_result[crf_result==0] = np.nan
        # crf_result = inpaint_nans(crf_result).astype('uint8')

        # for k in np.setdiff1d(np.unique(result2), unique_labels):
        #     result2[result2==k]=0

        # logging.info('Spatially filtered values inpainted')
        # logging.info('percent RAM usage: %f' % (psutil.virtual_memory()[2]))

        # result, unique_labels = do_classify(img,mask,n_sigmas,
        #                                     multichannel,intensity,edges,
        #                                     texture, sigma_min,sigma_max, rf_downsample_value)

        # n=len(unique_labels)
        # result = result.reshape(img.shape[0],img.shape[1],len(unique_labels))
        # # print(result.shape)
        # match = np.unique(np.argmax(result,-1))

