"""
Load data

Functions to load the data into TensorFlow
"""
import os
import math
import h5py
import random
import pathlib
import numpy as np
import pandas as pd
import tensorflow as tf

# Not-so-pretty code to feed data to TensorFlow.
class IteratorInitializerHook(tf.train.SessionRunHook):
    """Hook to initialise data iterator after Session is created.
    https://medium.com/onfido-tech/higher-level-apis-in-tensorflow-67bfb602e6c0"""
    def __init__(self):
        super(IteratorInitializerHook, self).__init__()
        self.iter_init_func = None

    def after_create_session(self, sess, coord):
        """Initialize the iterator after the session has been created."""
        self.iter_init_func(sess)

def _get_input_fn(features, labels, batch_size, evaluation=False, buffer_size=5000,
    eval_shuffle_seed=0):
    iter_init_hook = IteratorInitializerHook()

    def input_fn():
        # Input images using placeholders to reduce memory usage
        features_placeholder = tf.placeholder(features.dtype, features.shape)
        labels_placeholder = tf.placeholder(labels.dtype, labels.shape)
        dataset = tf.data.Dataset.from_tensor_slices((features_placeholder, labels_placeholder))

        if evaluation:
            dataset = dataset.shuffle(buffer_size, seed=eval_shuffle_seed).batch(batch_size)
        else:
            dataset = dataset.repeat().shuffle(buffer_size).batch(batch_size)

        iterator = dataset.make_initializable_iterator()
        next_data_batch, next_label_batch = iterator.get_next()

        # Need to initialize iterator after creating a session in the estimator
        iter_init_hook.iter_init_func = lambda sess: sess.run(iterator.initializer,
                feed_dict={features_placeholder: features, labels_placeholder: labels})

        return next_data_batch, next_label_batch
    return input_fn, iter_init_hook

# Load a time-series dataset. This is set up to load data in the format of the
# UCR time-series datasets (http://www.cs.ucr.edu/~eamonn/time_series_data/).
# Or, see the generate_trivial_datasets.py for a trivial dataset.
#
# Also runs through one_hot
def load_data(filename):
    """
    Load CSV files in UCR time-series data format

    Returns:
        data - numpy array with data of shape (num_examples, num_features)
        labels - numpy array with labels of shape: (num_examples, 1)
    """
    df = pd.read_csv(filename, header=None)
    df_data = df.drop(0, axis=1).values.astype(np.float32)
    df_labels = df.loc[:, df.columns == 0].values.astype(np.uint8)
    return df_data, df_labels

def one_hot(x, y, num_classes, index_one=False):
    """
    We want x to be floating point and of dimension [time_steps,num_features]
    where num_features is at least 1. If only a 1D array, then expand dimensions
    to make it [time_steps, 1].

    Also, we want y to be one-hot encoded. Though, note that for the UCR datasets
    (and my synthetic ones that I used the UCR dataset format for), it's indexed
    by 1 not 0, so we subtract one from the index. But, for most other datasets,
    it's 0-indexed.

    If np.squeeze(y) is already 2D (i.e. second dimension has more than 1 class),
    we'll skip one-hot encoding, assuming that it already is. Then we just convert
    to float32.
    """
    # Floating point
    x = x.astype(np.float32)

    # For if we only have one feature,
    # [batch_size, time_steps] --> [batch_size, time_steps, 1]
    if len(x.shape) < 3:
        x = np.expand_dims(x, axis=2)

    # One-hot encoded if not already 2D
    squeezed = np.squeeze(y)
    if len(squeezed.shape) < 2:
        if index_one:
            y = np.eye(num_classes, dtype=np.float32)[squeezed.astype(np.int32) - 1]
        else:
            y = np.eye(num_classes, dtype=np.float32)[squeezed.astype(np.int32)]
    else:
        y = y.astype(np.float32)
        assert squeezed.shape[1] == num_classes, "y.shape[1] != num_classes"

    return x, y

def tf_domain_labels(label, batch_size):
    """ Generate one-hot encoded labels for which domain data is from (using TensorFlow) """
    return tf.tile(tf.one_hot([0], depth=2), [batch_size,1])

def domain_labels(label, batch_size):
    """ Generate one-hot encoded labels for which domain data is from (using numpy) """
    return np.tile(np.eye(2)[label], [batch_size,1])

def shuffle_together(a, b, seed=None):
    """ Shuffle two lists in unison https://stackoverflow.com/a/13343383/2698494 """
    assert len(a) == len(b), "a and b must be the same length"
    rand = random.Random(seed)
    combined = list(zip(a, b))
    rand.shuffle(combined)
    return zip(*combined)

def shuffle_together_np(a, b, seed=None):
    """ Shuffle two numpy arrays together https://stackoverflow.com/a/4602224/2698494"""
    assert len(a) == len(b), "a and b must be the same length"
    rand = np.random.RandomState(seed)
    p = rand.permutation(len(a))
    return a[p], b[p]

# Load sleep paper datasets (RF data)
def load_data_sleep(dir_name, domain_a_percent=0.7, train_percent=0.7, seed=0):
    """
    Loads sleep RF data files in dir_name/*.npy
    Then split into training/testing sets using the specified seed for repeatability.

    We'll split the data twice. First, we split into domain A and domain B based
    on subjects (so no subject will be in both domains). Then, we concatenate all
    the data for each domain and randomly split into training and testing sets.

    Notes:
        - RF data is 30 seconds of data sampled at 25 samples per second, thus
          750 samples. For each of these sets of 750 samples there is a stage
          label.
        - The RF data is complex, so we'll split the complex 5 features into
          the 5 real and then 5 imaginary components to end up with 10 features.
    """
    #
    # Get data from data files grouped by subject
    #
    files = pathlib.Path(dir_name).glob("*.npy")
    subject_x = {}
    subject_y = {}

    for f in files:
        # Extract data from file
        d = np.load(f).item()
        subject = d['subject']
        stage_labels = d['stage']
        rf = d['rf']

        # Split 5 complex features into 5 real and 5 imaginary, i.e. now we
        # have 10 features
        rf = np.vstack([np.real(rf), np.imag(rf)])

        assert stage_labels.shape[0]*750 == rf.shape[-1], \
            "If stage labels is of shape (n) then rf should be of shape (5, 750n)"

        # Reshape and transpose into desired format
        x = np.transpose(np.reshape(rf, (rf.shape[0], -1, stage_labels.shape[0])))

        # Drop those that have a label other than 0-5 (sleep stages) since
        # label 6 means "no signal" and 9 means "error"
        no_error = stage_labels < 6
        x = x[no_error]
        stage_labels = stage_labels[no_error]

        assert x.shape[0] == stage_labels.shape[0], \
            "Incorrect first dimension of x (not length of stage labels)"
        assert x.shape[1] == 750, \
            "Incorrect second dimension of x (not 750)"
        assert x.shape[2] == 10, \
            "Incorrect third dimension of x (not 10)"

        # Group data by subject, stacking new data at bottom of old data
        if subject not in subject_x:
            subject_x[subject] = np.copy(x)
            subject_y[subject] = np.copy(stage_labels)
        else:
            subject_x[subject] = np.vstack([subject_x[subject], x])
            subject_y[subject] = np.hstack([subject_y[subject], stage_labels])

    #
    # Split subjects into training vs. testing and concatenate all the
    # data into training and testing sets
    #
    # Shuffle the subject ordering (using our seed for repeatability)
    xs = list(subject_x.values())
    ys = list(subject_y.values())
    xs, ys = shuffle_together(xs, ys, seed)

    # Split into two domains such that no subject is in both
    domain_end = math.ceil(domain_a_percent*len(xs))

    domain_a_x = xs[:domain_end]
    domain_b_x = xs[domain_end:]

    domain_a_y = ys[:domain_end]
    domain_b_y = ys[domain_end:]

    # Concatenate all the data from subjects
    a_x = np.vstack(domain_a_x)
    a_y = np.hstack(domain_a_y).astype(np.int32)
    b_x = np.vstack(domain_b_x)
    b_y = np.hstack(domain_b_y).astype(np.int32)

    # Shuffle data, using our seed
    a_x, a_y = shuffle_together_np(a_x, a_y, seed+1)
    b_x, b_y = shuffle_together_np(b_x, b_y, seed+2)

    # Split into training and testing sets
    training_end_a = math.ceil(train_percent*len(a_y))
    training_end_b = math.ceil(train_percent*len(b_y))

    train_data_a = a_x[:training_end_a]
    train_data_b = b_x[:training_end_b]
    test_data_a = a_x[training_end_a:]
    test_data_b = b_x[training_end_b:]

    train_labels_a = a_y[:training_end_a]
    train_labels_b = b_y[:training_end_b]
    test_labels_a = a_y[training_end_a:]
    test_labels_b = b_y[training_end_b:]

    return train_data_a, train_labels_a, \
        test_data_a, test_labels_a, \
        train_data_b, train_labels_b, \
        test_data_b, test_labels_b

# Load MIMIC-III time-series datasets
def load_data_mimiciii_ahrf(data_path="datasets/process-mimic-iii/Data/admdata_17f",
    hrs=24, label_type=0, fold=0):
    """
    Load MIMIC-III time-series data:
    domain adaptation on age for predicting mortality of adult AHRF patients

    WARNING: still haven't figure out how to get only the AHRF patients. Number
    of patients in each group doesn't match the paper's.

    - data_path = where 24hrs, 48hrs, series, etc. folders are
    - hrs = 24 or 48, depending on which data you want to use
    - label_type chooses which mortality, options are:
        - in-hospital (label_type=0)
        - 1-day (label_type=1)
        - 2-day (label_type=2)
        - 3-day (label_type=3)
        - 30-day (label_type=4)
        - 1-year (label_type=5)
    - We won't use folds at the moment except to pick data in the training vs.
      testing sets. We'll use validation as the testing set.
    - not using fold stats at the moment
    - not doing cross validation at the moment, so just pick a fold to use
    - our domains will be based on age, similar to the paper

    Based on: https://github.com/USC-Melady/Benchmarking_DL_MIMICIII/blob/master/Codes/DeepLearningModels/python/betterlearner.py
    """
    data_filename = os.path.join(data_path, "%dhrs" % hrs,
        "series", "imputed-normed-ep_1_%d.npz" % hrs)
    folds_filename = os.path.join(data_path, "%dhrs" % hrs,
        "series", "5-folds.npz")
    merged_filename = os.path.join(data_path, "%dhrs" % hrs,
        "DB_merged_%dhrs.npy" % hrs)
    icd9_filename = os.path.join(data_path, "%dhrs" % hrs,
        "ICD9-%dhrs.npy" % hrs)

    # Load all the required .npz files
    data_file = np.load(data_filename)
    folds_file = np.load(folds_filename)

    # Not using fold stats
    #folds_stat_file = np.load(folds_stat_filename)
    #folds_stat = folds_stat_file['folds_ep_mor'][label_type]

    # Get time-series data and labels
    x = data_file['ep_tdata']

    adm_labels = data_file['adm_labels_all']
    y = adm_labels[:, label_type]

    # non-time-series data -- shape = [admid, features=5]
    # the 5 features:
    #   - age(days)
    #   - acquired immunodeficiency syndrome
    #   - hematologic malignancy
    #   - metastatic cancer
    #   - admission type
    adm_features = data_file['adm_features_all']
    age = adm_features[:,0] / 365.25

    # Get rid of Nans that cause training problems
    adm_features[np.isinf(adm_features)] = 0
    adm_features[np.isnan(adm_features)] = 0
    x[np.isinf(x)] = 0
    x[np.isnan(x)] = 0

    # We want to find patients with acute hypoxemic respiratory failure (AHRF).
    # To get these patients, we need to check three things (see Khemani et al.
    # "Effect of tidal volume in children with acute hypoxemic respiratory
    # failure", paragraph about "Patient selection"):
    #  - acute onset
    #  - PF ratio <300
    #  - no left ventricular dysfunction (i.e. PF ratio <300 was due to lungs
    #    not the heart)

    # Find PF ratio (without averaging over time period, which is in x above)
    # See: 11_get_time_series_sample_17-features-processed_24hrs.ipynb
    PAO2_VAR = 4
    FIO2_VAR = 5

    data_all = np.empty([0], dtype=object)
    data_all = np.concatenate((data_all, np.load(merged_filename)))
    # 13 features (2 of which are PaO2 and FiO2 measurements)
    X_raw_p48 = np.array([np.array(xx, dtype=float)[:,:-2] for xx in data_all])
    # Times, so we can figure out which stayed for over >24 hours
    tsraw_p48 = np.array([np.array(xx, dtype=float)[:,-2] for xx in data_all])
    del data_all
    idx_x = np.where([(tt[-1] - tt[0]) > 1.0*60*60*hrs for tt in tsraw_p48])[0]
    tsraw = tsraw_p48[idx_x]
    X_raw = X_raw_p48[idx_x]

    # Get just these two measurements for all patients, nans if weren't measured
    pao4 = np.array([row[:,PAO2_VAR] for row in X_raw])
    fio2 = np.array([row[:,FIO2_VAR] for row in X_raw])

    # Determine which patients have at least one non-nan in the same place
    # in both PaO4 and FiO2 so we can compute the ratio
    #
    # This is not all that pretty since each patient may have a different number
    # of measurements (i.e. not 2D but a numpy array of variable-length arrays)
    has_measurement = np.zeros((len(pao4)), dtype=np.bool)
    has_lt_300 = np.zeros((len(pao4)), dtype=np.bool)
    assert len(pao4) == len(fio2), "Different length PaO4 and FiO2 arrays"
    for i in range(len(pao4)):
        # Set to 1 if both have at least one position with a non-nan
        has_measurement[i] = np.max(~(np.isnan(pao4[i])|np.isnan(fio2[i])))

        if has_measurement[i]:
            # If measurement, set to 1 if min ratio <300 at least once
            # Turns out, this is actually *all* of the ones that have a
            # measurement
            has_lt_300[i] = np.nanmin(pao4[i]/fio2[i]) < 300

    # Get diagnosis codes to determine if related to heart attack, which we will
    # exclude. See: 8_processing.nbconvert.ipynb and Wikipedia's list of
    # categories: https://en.wikipedia.org/wiki/List_of_ICD-9_codes
    # Important part: 6 is circulatory, so exclude those
    #
    # Doesn't work. Maybe see: Major et al. "Reusable Filtering Functions for
    # Application in ICU data: a case study"
    CIRCULATORY = 6
    RESPIRATORY = 7

    label_icd9_all = np.empty([0], dtype=object)
    label_icd9_all = np.concatenate((label_icd9_all, np.load(icd9_filename)))
    label_icd9_all = label_icd9_all[idx_x]
    # Each item in the list is: [aid,icd,numstr,category]
    # Get only those patients without a 6 in the list
    no_heart_problem = np.zeros((len(label_icd9_all)), dtype=np.bool)
    respiratory_problem = np.zeros((len(label_icd9_all)), dtype=np.bool)
    for i in range(len(label_icd9_all)):
        category = np.array(label_icd9_all[i])[:,3].astype(int)
        no_heart_problem[i] = CIRCULATORY not in category
        respiratory_problem[i] = RESPIRATORY in category

    # TODO even with either no_heart_problem or respiratory_problem, the numbers
    # still don't match.

    # Groups have roughly the same percentages as mentioned in paper
    # Sanity check lengths against what paper states:
    #    len(group2[0]),len(group3[0]),len(group4[0]),len(group5[0])
    #
    # Group 2: working-age adult (20 to 45 yrs, 508 patients)
    group2 = np.where((age >= 20) & (age < 45) & has_lt_300) # actually 889
    # Group 3: old working-age adult (46 to 65 yrs, 1888 patients)
    group3 = np.where((age >= 45) & (age < 65) & has_lt_300) # actually 2680
    # Group 4: elderly (66 to 85 yrs, 2394 patients)
    group4 = np.where((age >= 65) & (age < 85) & has_lt_300) # actually 3402
    # Group 5: old elderly (85 yrs and up, 437 patients)
    group5 = np.where((age >= 85) & has_lt_300) # actually 469

    # Sanity check, this should give 13.84% as stated in the paper, the
    # total mortality rate for the entire adult AHRF dataset.
    #     from functools import reduce
    #     a=reduce(np.union1d, (group2,group3,group4,group5))
    #     np.sum(y[a])/len(y[a])

    # TODO also check the percentages of *each fold* since we're only using fold 0
    # which may drastically differ since the folds weren't created taking into
    # consideration AHRF!

    # R-DANN should get ~0.821 accuracy and VRADA 0.770
    domain_a = group4
    domain_b = group3

    # Get the information about the folds
    TRAINING = 0
    #VALIDATION = 1
    TESTING = 2

    training_folds = folds_file['folds_ep_mor'][label_type,0,:,TRAINING]
    #validation_folds = folds_file['folds_ep_mor'][label_type,0,:,VALIDATION]
    testing_folds = folds_file['folds_ep_mor'][label_type,0,:,TESTING]

    training_indices = training_folds[fold]
    #validation_indices = validation_folds[fold]
    testing_indices = testing_folds[fold]

    # Split data
    train_data_a = x[np.intersect1d(domain_a, training_indices)]
    train_labels_a = y[np.intersect1d(domain_a, training_indices)]
    test_data_a = x[np.intersect1d(domain_a, testing_indices)]
    test_labels_a = y[np.intersect1d(domain_a, testing_indices)]
    train_data_b = x[np.intersect1d(domain_b, training_indices)]
    train_labels_b = y[np.intersect1d(domain_b, training_indices)]
    test_data_b = x[np.intersect1d(domain_b, testing_indices)]
    test_labels_b = y[np.intersect1d(domain_b, testing_indices)]

    return train_data_a, train_labels_a, \
        test_data_a, test_labels_a, \
        train_data_b, train_labels_b, \
        test_data_b, test_labels_b

def load_data_mimiciii_icd9(data_path="datasets/process-mimic-iii/Data/admdata_99p",
    hrs=48, fold=0):
    """
    Load MIMIC-III time-series data:
    domain adaptation on age for predicting mortality of adult AHRF patients

    - data_path = where 24hrs, 48hrs, series, etc. folders are
    - hrs = 24 or 48, depending on which data you want to use
    - We won't use folds at the moment except to pick data in the training vs.
      testing sets. We'll use validation as the testing set.
    - not using fold stats at the moment
    - not doing cross validation at the moment, so just pick a fold to use
    - our domains will be based on age, similar to the paper

    Based on: https://github.com/USC-Melady/Benchmarking_DL_MIMICIII/blob/master/Codes/DeepLearningModels/python/betterlearner.py
    """
    data_filename = os.path.join(data_path, "%dhrs_raw" % hrs,
        "series", "imputed-normed-ep_1_%d.npz" % hrs)
    folds_filename = os.path.join(data_path, "%dhrs_raw" % hrs,
        "series", "5-folds.npz")

    # Load all the required .npz files
    data_file = np.load(data_filename)
    folds_file = np.load(folds_filename)

    # Get time-series data and labels
    x = data_file['ep_tdata']
    y = data_file['y_icd9']

    # non-time-series data -- shape = [admid, features=5]
    # the 5 features:
    #   - age(days)
    #   - acquired immunodeficiency syndrome
    #   - hematologic malignancy
    #   - metastatic cancer
    #   - admission type
    adm_features = data_file['adm_features_all']
    age = adm_features[:,0] / 365.25

    # Get rid of Nans that cause training problems
    adm_features[np.isinf(adm_features)] = 0
    adm_features[np.isnan(adm_features)] = 0
    x[np.isinf(x)] = 0
    x[np.isnan(x)] = 0

    # In the paper they said they had 1 time-series data point for every 2 hours
    # and ignored anything after 24 hours, i.e. time-series was of length 12.
    #
    # Ignore anything after 24 hours
    x = x[:,:24,:]

    # Average every 2 values
    #  - move dimension of 24 to be last
    #  - reshape last dimension of 24 to be 24x2 (adding another dimension)
    #  - average over the last dimension
    #  - transpose the new dimension of 12 to be in the middle again
    x = np.transpose(x, (0, 2, 1)).reshape((x.shape[0], x.shape[2], 12, 2))
    x = x.mean(axis=3)
    x = np.transpose(x, (0, 2, 1))

    # Groups have roughly the same percentages as mentioned in paper
    # Sanity check lengths against what paper states:
    #    len(group2[0]),len(group3[0]),len(group4[0]),len(group5[0])
    #
    # Group 2: working-age adult (20 to 45 yrs)
    group2 = np.where((age >= 20) & (age < 45)) # 4586
    # Group 3: old working-age adult (46 to 65 yrs)
    group3 = np.where((age >= 45) & (age < 65)) # 11509
    # Group 4: elderly (66 to 85 yrs)
    group4 = np.where((age >= 65) & (age < 85)) # 14262
    # Group 5: old elderly (85 yrs and up)
    group5 = np.where((age >= 85)) # 3597

    # Sanity check, we should have a total of 19714 as stated in the paper.
    #     from functools import reduce
    #     len(reduce(np.union1d, (group2,group3,group4,group5)))

    # R-DANN should get ~0.616 AUC and VRADA ~0.623 on test set
    domain_a = group4
    domain_b = group3

    # Get the information about the folds
    TRAINING = 0
    #VALIDATION = 1
    TESTING = 2

    training_folds = folds_file['folds_ep_mor'][0,0,:,TRAINING]
    #validation_folds = folds_file['folds_ep_mor'][0,0,:,VALIDATION]
    testing_folds = folds_file['folds_ep_mor'][0,0,:,TESTING]

    training_indices = training_folds[fold]
    #validation_indices = validation_folds[fold]
    testing_indices = testing_folds[fold]

    # Split data
    train_data_a = x[np.intersect1d(domain_a, training_indices)]
    train_labels_a = y[np.intersect1d(domain_a, training_indices)]
    test_data_a = x[np.intersect1d(domain_a, testing_indices)]
    test_labels_a = y[np.intersect1d(domain_a, testing_indices)]
    train_data_b = x[np.intersect1d(domain_b, training_indices)]
    train_labels_b = y[np.intersect1d(domain_b, training_indices)]
    test_data_b = x[np.intersect1d(domain_b, testing_indices)]
    test_labels_b = y[np.intersect1d(domain_b, testing_indices)]

    return train_data_a, train_labels_a, \
        test_data_a, test_labels_a, \
        train_data_b, train_labels_b, \
        test_data_b, test_labels_b

# Load our watch activity prediction dataset
def load_data_watch(dir_name="datasets/watch"):
    """
    Loads watch activity prediction dataset
    """
    # files = pathlib.Path(dir_name).glob("*.npy")

    # for f in files:
    #     # Extract data from file
    #     d = np.load(f).item()
    #     name = d["name"]
    #     times = d["times"]
    #     features = d["features"]
    #     labels = d["labels"]

    # return train_data_a, train_labels_a, \
    #     test_data_a, test_labels_a, \
    #     train_data_b, train_labels_b, \
    #     test_data_b, test_labels_b
    raise NotImplementedError

def load_npy(filename, encoding='latin1'):
    """
    Load x,y data from npy file

    We specifically use latin1 encoding since it's pickled in Python 2 and
    unpickled here in Python 3. Otherwise we get an "'ascii' codec can't decode
    byte" error. See: https://stackoverflow.com/a/11314602
    """
    d = np.load(filename, encoding=encoding).item()
    features = d["features"]
    labels = d["labels"]
    return features, labels

def load_hdf5(filename):
    """
    Load x,y data from hdf5 file
    """
    d = h5py.File(filename, "r")
    features = np.array(d["features"])
    labels = np.array(d["labels"])
    return features, labels

def create_windows(x, y, window_size):
    """
    Concatenate along dim-1 to meet the desired window_size (e.g. window 0
    will be a list of examples 0,1,2,3,4 and the label of example 4). We'll
    skip any windows that reach beyond the end.
    """
    windows_x = []
    windows_y = []

    for i in range(len(y)-window_size):
        # Make it (1,window_size,# features)
        window_x = np.expand_dims(np.concatenate(x[i:i+window_size], axis=0), axis=0)
        window_y = y[i+window_size-1]

        windows_x.append(window_x)
        windows_y.append(window_y)

    windows_x = np.vstack(windows_x)
    windows_y = np.hstack(windows_y)

    return windows_x, windows_y

# Load our smart home activity prediction dataset
def load_data_home(dir_name="datasets/smarthome", A="ihs95", B="ihs117",
    train_percent=0.7, seed=0, window_size=5):
    """
    Loads watch activity prediction dataset
    """
    # Get A and B domain data/labels
    files = pathlib.Path(dir_name).glob("*.hdf5")
    a_x = None
    b_x = None

    for f in files:
        if f.stem == A:
            a_x, a_y = load_hdf5(f)
        elif f.stem == B:
            b_x, b_y = load_hdf5(f)

    assert a_x is not None and b_x is not None, "Must find A and B domains"

    # Expand dimensions to be (# examples, 1, # features)
    a_x = np.expand_dims(a_x, axis=1)
    b_x = np.expand_dims(b_x, axis=1)

    # Concatenate along dim-1 to meet the desired window_size (e.g. window 0
    # will be a list of examples 0,1,2,3,4 and the label of example 4). We'll
    # skip any windows that reach beyond the end.
    #
    # Note: above we expanded to be window_size==1, so if that's the case, we're
    # already done.
    if window_size != 1:
        a_x, a_y = create_windows(a_x, a_y, window_size)
        b_x, b_y = create_windows(b_x, b_y, window_size)

    # Shuffle data (using our seed for repeatability)
    a_x, a_y = shuffle_together_np(a_x, a_y, seed)
    b_x, b_y = shuffle_together_np(b_x, b_y, seed+1)

    # Split into training and testing sets
    training_end_a = math.ceil(train_percent*len(a_y))
    training_end_b = math.ceil(train_percent*len(b_y))

    train_data_a = a_x[:training_end_a]
    train_data_b = b_x[:training_end_b]
    test_data_a = a_x[training_end_a:]
    test_data_b = b_x[training_end_b:]

    train_labels_a = a_y[:training_end_a]
    train_labels_b = b_y[:training_end_b]
    test_labels_a = a_y[training_end_a:]
    test_labels_b = b_y[training_end_b:]

    return train_data_a, train_labels_a, \
        test_data_a, test_labels_a, \
        train_data_b, train_labels_b, \
        test_data_b, test_labels_b

if __name__ == "__main__":
    load_data_home()
    #x, y = load_hdf5("datasets/smarthome/ihs118.hdf5")
