#!/usr/bin/env python

import os
import os.path
import argparse
import logging
from collections import defaultdict

import numpy as np
import pandas as pd
from sklearn import metrics
from scipy.stats import linregress
from sklearn.preprocessing import normalize
from sklearn.grid_search import ParameterGrid

from utdeftvs import load_numpy

import fold
import models

from custom_classifiers import ThresholdClassifier, SuperTreeClassifier
tc = ThresholdClassifier()

N_FOLDS = 20

# before anything else, configure the logger
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s   %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

def longest_vector_posmatch(search, space):
    if not hasattr(space, 'posless'):
        setattr(space, 'posless', defaultdict(dict))
        for i, v in enumerate(space.vocab):
            sep = v.rindex('/')
            word, pos = v[:sep], v[sep+1:]
            space.posless[word][pos] = space.magn[i]
    if search not in space.posless:
        return None
    options = space.posless[search]
    pos = max(options.keys(), key=options.__getitem__)
    return search + '/' + pos

def always_nn_posmatch(search, space):
    return search + '/NN'

best_pos_match = always_nn_posmatch
#best_pos_match = longest_vector_posmatch

def consolidate(list_of_dicts):
    """
    Returns a dict of lists from a list of dicts
    """
    consolidated = {}
    for k in list_of_dicts[0].keys():
        consolidated[k] = np.array([d[k] for d in list_of_dicts])
    return consolidated

def feature_extraction(X, y, model, space, data):
    np.set_printoptions(precision=3, suppress=True)
    n_output = 10
    model.fit(X, y)
    data_vocab = set(list(data.word1) + list(data.word2))
    D = space.matrix.shape[1]
    segments = model.coef_.shape[1] / D
    print "=== ORTHOG TEST ==="
    for s in xrange(segments):
        l = D*s
        r = D*(s+1)
        subset = normalize(model.coef_[:,l:r])
        print subset.dot(subset.T)
    for coef_i in xrange(len(model.coef_)):
        print "=== COEF #%d ===" % coef_i
        full_magn = np.sum(np.square(model.coef_[coef_i]))
        for s in xrange(segments):
            l = D*s
            r = D*(s+1)
            feats = model.coef_[coef_i,l:r]
            p = np.sum(np.square(feats)) / full_magn
            print "Segment #%d [%d-%d] [%2.1f%%]" % (s + 1, l, r, 100*p)
            word_ranks = space.matrix.dot(feats)
            sorted = word_ranks.argsort()
            for i in xrange(n_output):
                fidx = sorted[-(i+1)]
                ridx = sorted[i]
                fscore = word_ranks[fidx]
                rscore = word_ranks[ridx]
                fword = space.vocab[fidx][:30]
                rword = space.vocab[ridx][:30]
                findata = (fword in data_vocab) and '*' or ' '
                rindata = (rword in data_vocab) and '*' or ' '
                print "  %6.3f %s %-30s  %6.3f %s %-30s" % (fscore, findata, fword, rscore, rindata, rword)
            print
            ctx_ranks = space.cmatrix.dot(feats)
            sorted = ctx_ranks.argsort()
            for i in xrange(n_output):
                fidx = sorted[-(i+1)]
                ridx = sorted[i]
                fscore = ctx_ranks[fidx]
                rscore = ctx_ranks[ridx]
                fctx = space.cvocab[fidx]
                rctx = space.cvocab[ridx]
                fword = fctx[fctx.rindex('+')+1:]
                rword = rctx[rctx.rindex('+')+1:]
                findata = (fword in data_vocab) and '*' or ' '
                rindata = (rword in data_vocab) and '*' or ' '
                print "  %6.3f %s %-30s  %6.3f %s %-30s" % (fscore, findata, fctx, rscore, rindata, rctx)

def render_confusion_matrix(y_true, y_pred, labels):
    cm = metrics.confusion_matrix(y_true, y_pred) / float(len(y_true))
    output = []
    shortlabels = "abcdefghij"
    output += ["    " + "    ".join(a for a, b in zip(shortlabels, labels)) + " <- predicted as"]
    f = lambda v: ("%.2f" % v).lstrip("0").replace(".00", ".  ")
    for i, (s, r) in enumerate(zip(shortlabels, labels)):
        output += ["  " + "  ".join(f(v) for v in cm[i]) + "  | %s = %s" % (s, r)]
    return "\n".join(output)


def standard_experiment(data, X, y, model, hyper, args):
    # data with predictions
    dwp = data.copy()

    seed = 1
    logger.debug("       On seed: %d / %d" % (seed, 20))
    logger.debug("  Genenerating: %d folds" % N_FOLDS)
    rng = np.random.RandomState(seed)
    fold_key = 'fold_%02d' % seed
    pred_key = 'prediction_%02d' % seed
    pred_prob_key = 'probability_%02d' % seed

    # need our folds for cross validation
    folds = fold.generate_folds_lhs(rng, data, n_folds=N_FOLDS)
    train_sizes = np.array([len(f[0]) for f in folds], dtype=np.float)
    val_sizes = np.array([len(f[1]) for f in folds], dtype=np.float)
    test_sizes = np.array([len(f[2]) for f in folds], dtype=np.float)

    logger.debug("   Train sizes: %.1f" % np.mean(train_sizes))
    logger.debug("     Val sizes: %.1f" % np.mean(val_sizes))
    logger.debug("    Test sizes: %.1f" % np.mean(test_sizes))
    logger.debug(" Test-Tr ratio: %.1f%%" % np.mean(test_sizes*100./(train_sizes + test_sizes)))
    logger.debug("  Percent data: %.1f%%" % np.mean((train_sizes + test_sizes)*100./len(y)))

    # perform cross validation

    pooled_eval, predictions, cv_scores = cv_trials(X, y, folds, model, hyper)
    dwp['prediction'] = predictions['pred']
    dwp['foldno'] = predictions['foldno']
    dwp['correct'] = (dwp['prediction'] == dwp['label'])

    for i, v in enumerate(cv_scores['f1']):
        logger.info("  Fold %02d F1: %.3f" % (i + 1, v))

    logger.info("\nConfusion matrix:\n" + render_confusion_matrix(y, predictions['pred'], labels=data.label.cat.categories))

    for k in cv_scores.keys():
        mu = cv_scores[k].mean()
        sigma = cv_scores[k].std()
        logger.info(" %3s across CV: %.3f   %.3f" % (k.upper(), mu, sigma))
    logger.debug("")

    if args.output:
        dwp.to_csv("%s/exp:%s,data:%s,space:%s,model:%s.csv.bz2" % (
            args.output, args.experiment, args.data,
            os.path.basename(args.space),
            args.model
            ), index=False, compression='bz2')

def cv_trials(X, y, folds, model, hyper):
    N = len(y)

    cv_scores = []
    predictions = {
        'pred': np.zeros(N, dtype=np.bool),
        'proba': np.zeros(N),
        'foldno': np.zeros(N, dtype=np.int32) - 1,
    }
    pg = list(ParameterGrid(hyper))
    for foldno, (train, val, test) in enumerate(folds):
        train_X, train_y = X[train], y[train]
        val_X, val_y = X[val], y[val]
        test_X, test_y = X[test], y[test]
        best_params = None
        best_val_f1 = None
        for these_params in pg:
            model.set_params(**these_params)
            model.fit(train_X, train_y)
            this_val_f1 = metrics.f1_score(val_y, model.predict(val_X), average="weighted")
            if not best_params or this_val_f1 > best_val_f1:
                best_params = these_params
                best_val_f1 = this_val_f1
        if len(pg) > 1:
            model.set_params(**best_params)
            model.fit(train_X, train_y)
        train_f1 = metrics.f1_score(train_y, model.predict(train_X), average="weighted")

        preds_y = model.predict(test_X)
        predictions['pred'][test] = preds_y

        predictions['foldno'][test] = foldno

        fold_eval = {'f1': metrics.f1_score(test_y, preds_y, average="weighted"),
                      'p': metrics.precision_score(test_y, preds_y, average="weighted"),
                      'r': metrics.recall_score(test_y, preds_y, average="weighted"),
                      'a': metrics.accuracy_score(test_y, preds_y)}
        print "[%02d] Best hyper [train %.3f -> val %.3f -> test %.3f] %s" % (foldno, train_f1, best_val_f1, fold_eval['f1'], best_params)


        cv_scores.append(fold_eval)
        np.set_printoptions(suppress=True)

    # now we want to compute global evaluations, and consolidate metrics
    cv_scores = consolidate(cv_scores)

    preds_y = predictions['pred']
    pooled_eval = {'f1': metrics.f1_score(y, preds_y, average="weighted"),
                    'p': metrics.precision_score(y, preds_y, average="weighted"),
                    'r': metrics.recall_score(y, preds_y, average="weighted"),
                    'a': metrics.accuracy_score(y, preds_y)}

    return pooled_eval, predictions, cv_scores

def load_data(filename, space):
    data = pd.read_table(filename)
    data['word1'] = data['word1'].apply(lambda x: best_pos_match(x, space))
    data['word2'] = data['word2'].apply(lambda x: best_pos_match(x, space))

    mask1 = data.word1.apply(lambda x: x in space.lookup)
    mask2 = data.word2.apply(lambda x: x in space.lookup)

    T = len(data)
    M1T = np.sum(mask1)
    M2T = np.sum(mask2)
    data = data[mask1 & mask2].reset_index(drop=True)
    F = len(data)
    logger.debug("")
    logger.debug(" Data Filename: %s" % filename)
    logger.debug("    Total Data: %5d" % T)
    logger.debug("       LHS OOV: %5d ( %4.1f%% )" % (M1T, M1T*100./T))
    logger.debug("       RHS OOV: %5d ( %4.1f%% )" % (M2T, M2T*100./T))
    logger.debug("         Final: %5d ( %4.1f%% )" % (F, F*100./T))
    logger.debug("")

    if data.label.dtype != bool:
        cats = list(set(data.label))
        import ipdb; ipdb.set_trace()
        data['label'] =  data.label.astype('category', categories=cats)

    return data




def main():
    # Argument parsing
    parser = argparse.ArgumentParser('Lexical Entailment Classifier')
    parser.add_argument('--data', '-d', help='Input file')
    parser.add_argument('--space', '-s', help='Distributional space')
    parser.add_argument('--model', '-m', help='Model setup', choices=models.SETUPS.keys())
    parser.add_argument('--experiment', '-e', default='standard', choices=('standard', 'random', 'match_error', 'featext', 'strat', 'levy'))
    parser.add_argument('--stratifier')
    parser.add_argument('--output', '-o')
    args = parser.parse_args()

    logger.debug('Lexent Arguments: ')
    logger.debug(args)

    # Steps that are the same regardless of experiments
    logger.debug("Loading space")
    nn_space = load_numpy(args.space, insertblank=True)
    space = nn_space.normalize()

    # Handle vocabulary issues
    logger.debug("Reading data")
    data = load_data(args.data, space)

    logger.debug("         Model: %s" % args.model)
    model, features, hyper = models.load_setup(args.model)

    logger.debug("      Features: %s" % features)
    X, y = models.generate_feature_matrix(data, space, features)

    if args.experiment == 'standard':
        standard_experiment(data, X, y, model, hyper, args)
    elif args.experiment == 'featext':
        feature_extraction(X, y, model, space, data)





if __name__ == '__main__':
    main()
