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


def result_to_list(identity, results):
    lst = []
    for idx, session in enumerate(results["Task"]):
        lst.append([
            identity["task_order"],
            identity["method"],
            identity["cmd"],
            identity["train_session"],
            results["Task"][idx],
            results["#Test"][idx],
            results["#Correct"][idx],
            results["Precision"][idx],
            identity["solver_training_time"],
            identity["generator_training_time"],
        ])

    return lst

def save_order(result_folder, task_order, tasks):
    if task_order==0:
        fout = open(result_folder+"task_orders.txt", "w")
    else:
        fout = open(result_folder+"task_orders.txt", "a")

    fout.write(";".join(tasks)+"\n")
    fout.close()

def save_model(result_folder, identity, model, model_type):
    name = "t{task_order}-m{method}{c}-{type}.model".format(
        task_order=identity["task_order"],
        method=identity["method"],
        type=model_type,
        c=identity["cmd"])

    model.save_model(result_folder+name)
    pass


def save_results(result_folder, identity, results):

    fname = "_t{task_order}-m{method}{c}_results.tmp".format(
        task_order=identity["task_order"],
        method=identity["method"],
        c=identity["cmd"])

    fout = open(result_folder+fname, "w")
    
    for _ in results:
        for row in _:
            row = [str(r) for r in row]
            fout.write(",".join(row)+"\n")
    fout.close()



def run_model(identity, method, args, config, train_datasets, test_datasets, verbose=False):
    try:   
        result_folder = args.results_dir

        m, cmd = method

        results = []
        args.replay = m
        identity["method"] = m

        # Use cuda?
        cuda = torch.cuda.is_available()
        device = torch.device("cuda" if cuda else "cpu")

        # Set random seeds
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)
        if cuda:
            torch.cuda.manual_seed(args.seed)

        if m == "lwf":
            args.solver_distill = True
        elif m== "ewc":
            args.solver_ewc = True


        identity["cmd"] = str(cmd)

        model = GenerativeReplayLearner(args, 2, verbose=verbose)

        solver = Classifier(
            input_feat=config['feature'],
            classes=len(train_datasets[0].classes),
            fc_layers=args.solver_fc_layers, fc_units=args.solver_fc_units, 
            cuda=cuda,
            device=device,
        ).to(device)
        model.set_solver(solver)

    
        if m in ["mp-gan", "mp-wgan", "sg-cgan", "sg-cwgan"]:
            args.replay = "generative"
            if m in ["sg-cgan", "sg-cwgan"]:
                args.g_iters = 5000
            else:
                args.g_iters = 1000

            generator = arg_params.get_generator(m, config, cuda, device, args, init_n_classes=2)
            model.set_generator(generator)

            args.g_log = int(args.g_iters*0.05)

        else:
            generator = None
        
        if args.replay == "offline":
            all_data = None
            for task, train_dataset in enumerate(train_datasets, 1):
                for c in train_dataset.classes:
                    model.classmap.map(c)

                if all_data is None:
                    all_data = train_dataset
                else:
                    all_data = all_data.merge(train_dataset)

                if task==1:
                    continue
                
                newmodel = model.solver.add_output_units(len(train_dataset.classes))
                model.set_solver(newmodel, None)

            model.train_solver(None, all_data, None)
            result = model.test(None, test_datasets, verbose=verbose)
            results.append(result_to_list(identity, result))
        else: 

            prev_active_classes = []
            prev_dataset = None
            for task, train_dataset in enumerate(train_datasets, 1):
                identity["train_session"] = task

                if task>1:
                    if model.generator is not None:
                        model.generator.classes += len(train_dataset.classes)
                    newmodel = model.solver.add_output_units(len(train_dataset.classes))
                    model.set_solver(newmodel, model.solver)

                active_classes_index = model.get_active_classes_index(task)

                replayed_dataset = None
                if args.replay == "generative":
                    replayed_dataset = model.sample(prev_active_classes, 500)
                elif args.replay == "exact":
                    replayed_dataset = prev_dataset
                
                start = time.time()
                model.train_solver(task, train_dataset, replayed_dataset)
                training_time = time.time() - start

                identity["solver_training_time"] = training_time

                start = time.time()
                if (args.generative_model is None) and (args.replay == "generative"):
                    model.train_generator(task, train_dataset, replayed_dataset)
                training_time = time.time() - start

                identity["generator_training_time"] = training_time

                if prev_dataset is None:
                    prev_dataset = train_dataset
                else:
                    prev_dataset = prev_dataset.merge(train_dataset)

                prev_active_classes = model.classmap.classes
                
                result = model.test(task, test_datasets, verbose=verbose)
                results.append(result_to_list(identity, result))


            
        save_model(result_folder, identity, model.solver, "solver")
        if model.generator is not None:
            save_model(result_folder, identity, model.generator, "generator")
        

        save_results(result_folder, identity, results)

    except Exception as e:
        print("ERROR:", e)

    print("DONE task order", identity["task_order"])


def clearup_tmp_file(result_folder, ntask, methods, delete=True):

    fresult = open(result_folder+"results.txt", "w")
    fresult.write("task_order, method, cmd, train_session, task_index, no_of_test, no_of_correct_prediction, accuracy, solver_training_time, generator_training_time\n")
 
    for task_order in range(ntask):
        for method in methods:
            m, cmd = method

            fname = "_t{task_order}-m{method}{c}_results.tmp".format(
                task_order=task_order,
                method=m,
                c=str(cmd))

            try:
                fo = open(result_folder+fname)
                for line in fo:
                    fresult.write(line)
                fo.close()

                if delete:
                    os.remove(result_folder+fname)
            except Exception as e:
                print(e)

    fresult.close()

def select_dataset(args):
    if args.data_dir == "pamap":
        classes = ['lying', 'sitting', 'standing', 'ironing', 'vacuum cleaning', 'ascending stairs', 'walking', 'descending stairs', 'cycling', 'running']
        args.data_dir = "./Dataset/Dataset_PerCom18_STL/pamap.feat"
    else:
        classes = [
            "R1_work_at_computer",
            "R2_work_at_computer",
            "R1_sleep",
            "R2_sleep",
            "R1_bed_to_toilet",
            "R2_bed_to_toilet",

            "R2_prepare_dinner",
            "R2_watch_TV",
            
            "R2_prepare_lunch",
            "R1_work_at_dining_room_table",
        ]
        args.data_dir = "./Dataset/twor.2009/annotated.feat.ch5"
    
    return SmartHomeDataset(args.data_dir, classes=classes)

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
        ("offline", 0), ("none", 0), ("exact", 0), ("mp-gan", 0), ("mp-wgan", 0), ("sg-cgan", 0), ("sg-cwgan", 0), ("lwf", 0), ("ewc", 0),
        ("offline", 1), ("none", 1), ("exact", 1), ("mp-gan", 1), ("mp-wgan", 1), ("sg-cgan", 1), ("sg-cwgan", 1), ("lwf", 1), ("ewc", 1),
        ("offline", 2), ("none", 2), ("exact", 2), ("mp-gan", 2), ("mp-wgan", 2), ("sg-cgan", 2), ("sg-cwgan", 2), ("lwf", 2), ("ewc", 2),
        ("offline", 3), ("none", 3), ("exact", 3), ("mp-gan", 3), ("mp-wgan", 3), ("sg-cgan", 3), ("sg-cwgan", 3), ("lwf", 3), ("ewc", 3),
    ]

    jobs = []
    pool = mp.Pool()
    start = time.time()
    ntask = 10
    for task_order in range(ntask):
        
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
        save_order(result_folder, task_order, base_dataset.classes)

        
        traindata, testdata = base_dataset.train_test_split()

        dataset = traindata
        if args.oversampling:
            dataset = traindata.resampling()

        train_datasets, config, classes_per_task = traindata.split(tasks=args.tasks)
        test_datasets, _, _ = testdata.split(tasks=args.tasks)

        print("******* Run ",task_order,"*******")
        print("\n")

        for method in methods:
            m, cmd = method
            identity["method"] = m
            args = copy.deepcopy(args)
            
            args.critic_fc_units = (cmd+1)*1000
            args.generator_fc_units = (cmd+1)*1000

            # run_model(identity, method, args, config, train_datasets, test_datasets, True)
            pool.apply_async(run_model, args=(identity, method, args, config, train_datasets, test_datasets, True))
            
    pool.close()
    pool.join()


    training_time = time.time() - start
    print(training_time)

    clearup_tmp_file(result_folder, ntask, methods)