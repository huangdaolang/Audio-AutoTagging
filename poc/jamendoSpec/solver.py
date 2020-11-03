# -*- coding: utf-8 -*-
import os
import time
import numpy as np
import datetime
import tqdm
from sklearn import metrics
import pickle
import csv
import math

import torch
import torch.nn as nn

from model import CNN


class Solver(object):
    def __init__(self, data_loader, valid_loader, config, root):
        # Data loader
        self.data_loader = data_loader
        self.valid_loader = valid_loader

        # Training settings
        self.n_epochs = config.num_epochs
        self.lr = 1e-4
        self.log_step = 10
        self.is_cuda = torch.cuda.is_available()
        self.model_save_path = config.model_save_path
        self.batch_size = config.batch_size
        self.tag_list = self.get_tag_list(config, root)
        if config.subset == 'all':
            self.num_class = 183
        elif config.subset == 'genre':
            self.num_class = 87
            self.tag_list = self.tag_list[:87]
        elif config.subset == 'instrument':
            self.num_class = 40
            self.tag_list = self.tag_list[87:127]
        elif config.subset == 'moodtheme':
            self.num_class = 56
            self.tag_list = self.tag_list[127:]
        elif config.subset == 'top50tags':
            self.num_class = 50
        self.model_fn = os.path.join(self.model_save_path, 'best_model.pth')
        self.roc_auc_fn = 'roc_auc_'+config.subset+'_'+str(config.split)+'.npy'
        self.pr_auc_fn = 'pr_auc_'+config.subset+'_'+str(config.split)+'.npy'

        # Build model
        self.build_model()

    def build_model(self):
        # model and optimizer
        model = CNN(num_class=self.num_class)

        self.model = model
        if self.is_cuda:
            self.model.cuda()
        self.optimizer = torch.optim.Adam(self.model.parameters(), self.lr)

    def load(self, filename):
        S = torch.load(filename)
        self.model.load_state_dict(S)

    def save(self, filename):
        model = self.model.state_dict()
        torch.save({'model': model}, filename)

    def to_var(self, x):
        if self.is_cuda:
            x = x.cuda()
        return x

    def train(self):
        start_t = time.time()
        current_optimizer = 'adam'
        best_roc_auc = 0
        drop_counter = 0
        reconst_loss = nn.BCELoss()

        # Variables to keep track of time
        t_total = 0
        t_epoch = 0
        t_step = 0
        last_time = time.time()
        last_epoch = time.time()
        all_time = []
        all_time_epoch = []

        for epoch in range(self.n_epochs):
            t = time.time()
            t_step = t - last_time
            t_total += t_step
            last_time = t
            all_time.append(t_step)

            drop_counter += 1
            # train
            self.model.train()
            ctr = 0
            for x, y in self.data_loader:
                ctr += 1

                # variables to cuda
                x = self.to_var(x)
                y = self.to_var(y)

                # predict
                out = self.model(x)
                loss = reconst_loss(out, y)

                # back propagation
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

                # print log
                if (ctr) % self.log_step == 0:
                    print("[%s] Epoch [%d/%d] Iter [%d/%d] train loss: %.4f Elapsed: %s" %
                            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            epoch+1, self.n_epochs, ctr, len(self.data_loader), loss.item(),
                            datetime.timedelta(seconds=time.time()-start_t)))

            # validation
            roc_auc, _ = self._validation(start_t, epoch)

            # save model
            if roc_auc > best_roc_auc:
                print('best model: %4f' % roc_auc)
                best_roc_auc = roc_auc
                torch.save(self.model.state_dict(), os.path.join(self.model_save_path, 'best_model.pth'))

            # schedule optimizer
            current_optimizer, drop_counter = self._schedule(current_optimizer, drop_counter)

            t = time.time()
            t_epoch = t - last_epoch
            last_epoch = t
            all_time_epoch.append(t_epoch)

            print("---- Summary ----- Epoch [{}/{}], t_epoch = {:.4f}, t_total = {:.4f}".format(
                epoch + 1, self.n_epochs, t_epoch, t_total
            ))

        print("[%s] Train finished. Elapsed: %s"
                % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    datetime.timedelta(seconds=time.time() - start_t)))

    def _validation(self, start_t, epoch):
        prd_array = []  # prediction
        gt_array = []   # ground truth
        ctr = 0
        self.model.eval()
        reconst_loss = nn.BCELoss()
        for x, y in self.valid_loader:
            ctr += 1

            # variables to cuda
            x = self.to_var(x)
            y = self.to_var(y)

            # predict
            out = self.model(x)
            loss = reconst_loss(out, y)

            # print log
            if (ctr) % self.log_step == 0:
                print("[%s] Epoch [%d/%d], Iter [%d/%d] valid loss: %.4f Elapsed: %s" %
                        (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        epoch+1, self.n_epochs, ctr, len(self.valid_loader), loss.item(),
                        datetime.timedelta(seconds=time.time()-start_t)))

            # append prediction
            out = out.detach().cpu()
            y = y.detach().cpu()
            for prd in out:
                prd_array.append(list(np.array(prd)))
            for gt in y:
                gt_array.append(list(np.array(gt)))

        # get auc
        roc_auc, pr_auc, _, _ = self.get_auc_turbo(prd_array, gt_array, self.tag_list)
        return roc_auc, pr_auc

    def get_tag_list(self, config, root):
        if config.subset == 'top50tags':
            path = os.path.join(root, 'scripts/baseline/tag_list_50.npy')
        else:
            path = os.path.join(root, 'scripts/baseline/tag_list.npy')
        tag_list = np.load(path)
        return tag_list

    def get_auc(self, prd_array, gt_array):
        prd_array = np.array(prd_array)
        gt_array = np.array(gt_array)

        roc_aucs = metrics.roc_auc_score(gt_array, prd_array, average='macro')
        pr_aucs = metrics.average_precision_score(gt_array, prd_array, average='macro')

        print('roc_auc: %.4f' % roc_aucs)
        print('pr_auc: %.4f' % pr_aucs)

        roc_auc_all = metrics.roc_auc_score(gt_array, prd_array, average=None)
        pr_auc_all = metrics.average_precision_score(gt_array, prd_array, average=None)

        for i in range(self.num_class):
            print('%s \t\t %.4f , %.4f' % (self.tag_list[i], roc_auc_all[i], pr_auc_all[i]))
        return roc_aucs, pr_aucs, roc_auc_all, pr_auc_all

    def get_auc_turbo(self, y_preds, y_true, labels_list):
        print("===========================================")
        print("Scores")
        print("===========================================")

        y_true = np.array(y_true)
        y_preds = np.array(y_preds)

        print(y_true.sum(axis=0))
        print(y_true.shape)
        print(y_preds.shape)

        ## Fix y_true, if there are tags with count <= 0, remove those tags
        cols = y_true.sum(axis=0) <= 0
        y_true = y_true[:, ~cols]
        y_preds = y_preds[:, ~cols]

        if any(cols):
            print("WARNING, {} columns removed from scores computation.".format(len(cols[cols == True])))

        print(y_true.sum(axis=0))
        print(y_true.shape)
        print(y_preds.shape)

        score_accuracy = 0
        score_lwlrap = 0
        score_roc_auc = 0
        score_pr_auc = 0
        score_mse = 0
        score_roc_auc_all = np.zeros((len(labels_list), 1))
        score_pr_auc_all = np.zeros((len(labels_list), 1))
        try:
            # for accuracy, lets pretend this is a single label problem, so assign only one label to every prediction
            score_accuracy = 0
            score_lwlrap = metrics.label_ranking_average_precision_score(y_true, y_preds)
            score_mse = math.sqrt(metrics.mean_squared_error(y_true, y_preds))
            # Average precision is a single number used to approximate the integral of the PR curve
            score_pr_auc = metrics.average_precision_score(y_true, y_preds, average="macro")
            score_roc_auc = metrics.roc_auc_score(y_true, y_preds, average="macro")
        except ValueError as e:
            print("Soemthing wrong with evaluation")
            print(e)
        print("Accuracy =  %f" % (score_accuracy))
        print("Label ranking average precision =  %f" % (score_lwlrap))
        print("ROC_AUC score  = %f" % (score_roc_auc))
        print("PR_AUC score = %f" % (score_pr_auc))
        print("MSE score for train = %f" % (score_mse))
        # These are per tag
        try:
            score_roc_auc_all = metrics.roc_auc_score(y_true, y_preds, average=None)
            score_pr_auc_all = metrics.average_precision_score(y_true, y_preds, average=None)
        except ValueError as e:
            print("Soemthing wrong with evaluation")
            print(e)

        print("")
        print("Per tag, roc_auc, pr_auc")
        fixed_labels_list = list(np.array(labels_list)[~cols])
        for i in range(len(fixed_labels_list)):
            if cols[i]:  # Skip this tag if the count is <= 0
                continue
            print('%s \t\t\t %.4f \t%.4f' % (fixed_labels_list[i], score_roc_auc_all[i], score_pr_auc_all[i]))

        return score_roc_auc, score_pr_auc, score_roc_auc_all, score_pr_auc_all

    def _schedule(self, current_optimizer, drop_counter):
        if current_optimizer == 'adam' and drop_counter == 60:
            self.load(os.path.join(self.model_save_path, 'best_model.pth'))
            self.optimizer = torch.optim.SGD(self.model.parameters(), 0.001, momentum=0.9, weight_decay=0.0001, nesterov=True)
            current_optimizer = 'sgd_1'
            drop_counter = 0
            print('sgd 1e-3')
        # first drop
        if current_optimizer == 'sgd_1' and drop_counter == 20:
            self.load(os.path.join(self.model_save_path, 'best_model.pth'))
            for pg in self.optimizer.param_groups:
                pg['lr'] = 0.0001
            current_optimizer = 'sgd_2'
            drop_counter = 0
            print('sgd 1e-4')
        # second drop
        if current_optimizer == 'sgd_2' and drop_counter == 20:
            self.load(os.path.join(self.model_save_path, 'best_model.pth'))
            for pg in self.optimizer.param_groups:
                pg['lr'] = 0.00001
            current_optimizer = 'sgd_3'
            print('sgd 1e-5')
        return current_optimizer, drop_counter

    def test(self):
        start_t = time.time()
        reconst_loss = nn.BCELoss()
        epoch = 0

        self.load(self.model_fn)
        self.model.eval()
        ctr = 0
        prd_array = []  # prediction
        gt_array = []   # ground truth
        for x, y in self.data_loader:
            ctr += 1

            # variables to cuda
            x = self.to_var(x)
            y = self.to_var(y)

            # predict
            out = self.model(x)
            loss = reconst_loss(out, y)

            # print log
            if (ctr) % self.log_step == 0:
                print("[%s] Iter [%d/%d] test loss: %.4f Elapsed: %s" %
                        (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        ctr, len(self.data_loader), loss.item(),
                        datetime.timedelta(seconds=time.time()-start_t)))

            # append prediction
            out = out.detach().cpu()
            y = y.detach().cpu()
            for prd in out:
                prd_array.append(list(np.array(prd)))
            for gt in y:
                gt_array.append(list(np.array(gt)))

        # get auc
        roc_auc, pr_auc, roc_auc_all, pr_auc_all = self.get_auc_turbo(prd_array, gt_array, self.tag_list)

        # save aucs
        np.save(open(self.roc_auc_fn, 'wb'), roc_auc_all)
        np.save(open(self.pr_auc_fn, 'wb'), pr_auc_all)