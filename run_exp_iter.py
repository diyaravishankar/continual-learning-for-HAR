#!/usr/bin/env python3
import torch
import numpy as np
from smart_home_dataset import SmartHomeDataset
from classifier import Classifier
from torch import optim
import utils
import callbacks as cb
import time

from generative_replay_learner import GenerativeReplayLearner;
import arg_params
import json
import os
import copy

import torch.multiprocessing as mp
from run_main import *

def get_g_iter(method, cmd=None):
    return 1000*int(cmd)

def get_hidden_unit(args):
    if args.data_dir == "pamap":
        return 1000
    elif args.data_dir == "dsads":
        return 2000
    elif args.data_dir == "housea":
        return 100
    else:
        return 500

if __name__ == "__main__":

    parser = arg_params.get_parser()
    args = parser.parse_args()
    print("Arguments")
    print(args)

    result_folder = args.results_dir

    print("\n")
    print("STEP1: load datasets")

    base_dataset = select_dataset(args)
    


    methods = [ 
        ("offline", 0), ("none", 0), ("exact", 0), ("mp-gan", 0), ("mp-wgan", 0), ("sg-cgan", 0), ("sg-cwgan", 0),
        ("offline", 1), ("none", 1), ("exact", 1), ("mp-gan", 1), ("mp-wgan", 1), ("sg-cgan", 1), ("sg-cwgan", 1),
        ("offline", 2), ("none", 2), ("exact", 2), ("mp-gan", 2), ("mp-wgan", 2), ("sg-cgan", 2), ("sg-cwgan", 2),
        ("offline", 3), ("none", 3), ("exact", 3), ("mp-gan", 3), ("mp-wgan", 3), ("sg-cgan", 3), ("sg-cwgan", 3),
    ]

    jobs = []
    # pool = mp.Pool()
    start = time.time()
    ntask = 10

    tasks = []
    if args.task_order is not None:
        ft = open(args.task_order)
        tasks = [line.strip().split(";") for line in ft]

    base_args = args
    for task_order in range(ntask):
        
        if args.task_order is not None:
            base_dataset.permu_task_order(tasks[task_order])
        else:
            base_dataset.permu_task_order()

        identity = {
            "task_order": None,
            "method": None,
            "train_session": None,
            "task_index": None,
            "no_of_test": None,
            "no_of_correct_prediction": None,
            "accuracy": None,
            "solver_training_time": None,
            "generator_training_time": None,
        }
        
        
        identity["task_order"] = task_order
        if args.task_order is None:
            save_order(result_folder, task_order, base_dataset.classes)

        
        traindata, testdata = base_dataset.train_test_split()

        dataset = traindata

        if args.oversampling:
            dataset = traindata.resampling()
        
        
        train_datasets, config, classes_per_task = dataset.split(tasks=args.tasks)
        test_datasets, _, _ = testdata.split(tasks=args.tasks)


        print("******* Run ",task_order,"*******")
        print("\n")

        for method in methods:
            m, cmd = method
            identity["method"] = m
            args = copy.deepcopy(base_args)
            
            args.critic_fc_units = get_hidden_unit(args)
            args.generator_fc_units = get_hidden_unit(args)

            args.g_iters = get_g_iter(m, cmd+1)
            run_model(identity, method, args, config, train_datasets, test_datasets, True)
            # pool.apply_async(run_model, args=(identity, method, args, config, train_datasets, test_datasets, False))
            
    # pool.close()
    # pool.join()


    training_time = time.time() - start
    print(training_time)

    # clearup_tmp_file(result_folder, ntask, methods)