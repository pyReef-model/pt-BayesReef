 
  
#Main Contributers:   Rohitash Chandra, Ratneel Deo and Jodie Pall  Email: c.rohitash@gmail.com 

#rohitash-chandra.github.io

#  : Parallel tempering for multi-core systems - PT-BayesReef

#related: https://github.com/pyReef-model/pt-BayesReef


from __future__ import print_function, division
import multiprocessing
import gc

import os
import math
import time
import random
import csv
import numpy as np
from numpy import inf
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.mlab as mlab
from matplotlib.cm import terrain, plasma, Set2
from pylab import rcParams
from pyReefCore.model import Model
from pyReefCore import plotResults
from cycler import cycler
from scipy import stats 

import sys

from sys import getsizeof

cmap=plt.cm.Set2
c = cycler('color', cmap(np.linspace(0,1,8)) )
plt.rcParams["axes.prop_cycle"] = c

# import copy
# from copy import deepcopy
# from pylab import rcParams
# import collections
# import cmocean as cmo


# from scipy import special

# import fnmatch
# import shutil
# from PIL import Image
# from io import StringIO

# mpl.use('Agg')

# from matplotlib.patches import Polygon
# from matplotlib.collections import PatchCollection

# from scipy.spatial import cKDTree
# from mpl_toolkits.axes_grid1 import make_axes_locatable
# from mpl_toolkits.mplot3d import Axes3D

# import itertools

# import sys
# import plotly
# import plotly.plotly as py
# from plotly.graph_objs import *
# plotly.offline.init_notebook_mode()
# from plotly.offline.offline import _plot_html


class ptReplica(multiprocessing.Process):
	def __init__(self, samples,filename,xmlinput,vis,num_communities, vec_parameters, realvalues, maxlimits_vec,minlimits_vec,stepratio_vec,
		check_likelihood,swap_interval,simtime, c_pr_flow, c_pr_sed, gt_depths, gt_vec_d, gt_timelay, gt_vec_t, gt_prop_t, gt_prop_d, tempr, parameter_queue,event , main_proc, burn_in):

		#--------------------------------------------------------
		multiprocessing.Process.__init__(self)

		self.samples = samples
		self.filename = filename
		self.input = xmlinput  
		self.vis = vis
		self.communities = num_communities
		self.vec_parameters =  vec_parameters

		self.maxlimits_vec = maxlimits_vec
		self.minlimits_vec  = minlimits_vec
		self.stepratio_vec = np.asarray(stepratio_vec)
		self.check_likelihood =  check_likelihood 
		self.swap_interval = swap_interval
		self.simtime = simtime
		self.realvalues_vec = realvalues # true values of free parameters for comparision. Note this will not be avialable in real world application
		self.num_param =  maxlimits_vec.size
		self.c_pr_flow = c_pr_flow
		self.c_pr_sed = c_pr_sed

		self.gt_depths = gt_depths
		self.gt_vec_d = gt_vec_d
		self.gt_timelay = gt_timelay
		self.gt_vec_t = gt_vec_t
		self.gt_prop_t = gt_prop_t

		self.gt_prop_d = gt_prop_d

		self.temperature = tempr
		self.adapttemp  = tempr
		self.processID = tempr      
		self.parameter_queue = parameter_queue
		self.event = event
		self.signal_main = main_proc

		# self.run_nb = run_nb 
		self.burn_in = burn_in

		self.sedsim = True
		self.flowsim = True 
		self.d_sedprop = float(np.count_nonzero(gt_prop_t[:,num_communities]))/gt_prop_t.shape[0]

		self.font = 10
		self.width = 1 
		
		# self.core_depths = core_depths 
		# self.core_data =  core_data 
		self.runninghisto = False  # if you want to have histograms of the chains during runtime in pos_variables folder NB: this has issues in Artimis

		self.lhood_timestructure = False  # False indicates depth_structure in lhood

	def runModel(self, reef, input_vector):
		reef.convertVector(self.communities, input_vector, self.sedsim, self.flowsim) #model.py
		self.true_sed, self.true_flow = reef.load_xml(self.input, self.sedsim, self.flowsim)
		# if self.vis[0] == True:
			# reef.core.initialSetting(size=(8,2.5), size2=(8,3.5)) # View initial parameters
		reef.run_to_time(self.simtime,showtime=100.)
		# if self.vis[1] == True:
		#     reef.plot.drawCore(lwidth = 3, colsed=self.colors, coltime = self.colors2, size=(9,8), font=8, dpi=300)
		sim_output_t, sim_timelay = reef.plot.convertTimeStructure() #modelPlot.py
		sim_output_d = reef.plot.convertDepthStructure(self.communities, self.gt_depths)
		return sim_output_t, sim_output_d,sim_timelay

	def convertCoreFormat(self, core): # Convert model predictions to 1-D format

		vec = np.zeros(core.shape[0])
		for n in range(len(vec)):
			if not all(v == 0 for v in core[n,:]):
				idx = np.argmax(core[n,:])# get index,
				vec[n] = idx+1 # +1 so that zero is preserved as 'none'
			else:
				vec[n] = 5.
		return vec

	def diffScore(self, sim_data,synth_data,intervals):
		maxprop = np.zeros((intervals,sim_data.shape[1]))
		for n in range(intervals):
			idx_synth = np.argmax(synth_data[n,:])
			idx_sim = np.argmax(sim_data[n,:])
			if ((sim_data[n,self.communities] != 1.) and (idx_synth == idx_sim)): #where sediment !=1 and max proportions are equal:
				maxprop[n,idx_synth] = 1
		diff = (1- float(np.count_nonzero(maxprop))/intervals)*100
		return diff

	def rmse(self, sim, obs):
		# where there is 1 in the sed column, count
		sed = np.count_nonzero(sim[:,self.communities])
		p_sedprop = (float(sed)/sim.shape[0])
		sedprop = np.absolute((self.d_sedprop - p_sedprop)*0.5)
		rmse =(np.sqrt(((sim - obs) ** 2).mean()))*0.5
		return rmse + sedprop

	def modelOutputParameters(self, prop_t, vec_t, timelay):

		n = timelay.size # no. of data points in gt output #171
		s = 1
		cps = np.zeros(n)
		cps[0] = timelay[0] # (171,)
		ca_props = np.zeros((n, prop_t.shape[1]))# (171,5)

		for i in range(1,n):
			if vec_t[i] != vec_t[i-1]:
				cps[s] = timelay[i-1]
				ca_props[s-1] = prop_t[i-1,:]
				s += 1
			if i == n-1:
				cps[s] = timelay[i]
				ca_props[s-1] = prop_t[i,:]
		S = s
		cps = np.trim_zeros(cps, 'b') # append a zero on the end afterwards
		ca_props = ca_props[0:S,:]
		return S, cps, ca_props

	def noGrowthColumn(self, sim_prop):
		# Creates additional binary column that takes a value of 1 where there is no growth, otherwise 0.
		v_nogrowth = np.zeros((sim_prop.shape[0],1))
		for a in range(sim_prop.shape[0]):
			if np.amax(sim_prop[a,:]) == 0.:
				v_nogrowth[a,:] = 1.
		sim_prop = np.append(sim_prop,v_nogrowth,axis=1)
		return sim_prop

 

 


	def likelihoodWithProps(self, reef, gt_prop_d, input_v): #depth_structure in lhood
		sim_prop_t, sim_prop_d, sim_timelay = self.runModel(reef, input_v)
		sim_prop_d = sim_prop_d.T
		# sim_prop_d5 = self.noGrowthColumn(sim_prop_d)
		# sim_prop_d5 = sim_prop_d5.T
		intervals = sim_prop_d.shape[0]

		log_core = np.log(sim_prop_d+0.0001)
		log_core[log_core == -inf] = 0
		z = log_core * gt_prop_d
		likelihood = np.sum(z)
		diff = self.diffScore(sim_prop_d,gt_prop_d, intervals)
		sim_vec_d = self.convertCoreFormat(sim_prop_d)
		sim_vec_t = self.convertCoreFormat(sim_prop_t)
		# rmse = self.rmse(sim_prop_t5, gt_prop_d)
		return [likelihood *(1.0/self.adapttemp) , diff, sim_prop_d, sim_vec_d, sim_vec_t]
		   
	def likelihoodWithDominance(self, reef, gt_prop_t, input_v):  #time_structure in lhood
		sim_data_t, sim_data_d, sim_timelay = self.runModel(reef, input_v)
		sim_data_t5 = self.noGrowthColumn(sim_data_t)
		intervals = sim_data_t5.shape[0]
		z = np.zeros((intervals,sim_data_t5.shape[1]))    
		for n in range(intervals):
			idx_data = np.argmax(gt_prop_t[n,:])
			idx_model = np.argmax(sim_data_t5[n,:])
			if ((sim_data_t5[n,self.communities] != 1.) and (idx_data == idx_model)): #where sediment !=1 and max proportions are equal:
				z[n,idx_data] = 1
		diff = 1. - (float(np.count_nonzero(z))/intervals)# Difference score calculation
		z = z + 0.1
		z = z/(1+(1+self.communities)*0.1)
		log_z = np.log(z)
		likelihood = np.sum(log_z)
		# rmse = self.rmse(sim_data_t5, gt_prop_t)
		sim_vec_t = self.convertCoreFormat(sim_data_t5)
		sim_vec_d = self.convertCoreFormat(sim_data_d.T)
		return [likelihood *(1.0/self.adapttemp), diff, sim_data_t5, sim_data_d.T, sim_vec_t, sim_vec_d]

	def proposalJump(self, current, low_limit, high_limit, jump_width):
		proposal = current + np.random.normal(0, jump_width)
		if proposal >= high_limit:
			proposal = current
		elif proposal <= low_limit:
			proposal = current
		return proposal

	def run(self):
		# Note this is a chain that is distributed to many cores. The chain is also known as Replica in Parallel Tempering
		
		samples = self.samples

		pt_samples = (self.samples * 0.4)


		sedlim = [self.minlimits_vec[0], float(self.maxlimits_vec[0])]
		flowlim = [self.minlimits_vec[12], float(self.maxlimits_vec[12])]


		gt_prop_t = self.gt_prop_t 
		gt_prop_d = self.gt_prop_d 

		gt_vec_t = self.gt_vec_t 
		gt_timelay = self.gt_timelay
		gt_vec_d = self.gt_vec_d
		gt_depths = self.gt_depths
		c_pr_flow = self.c_pr_flow
		c_pr_sed = self.c_pr_sed
		communities = self.communities
		num_param = self.num_param
		burnsamples = int(samples*self.burn_in)
		print('sedlim', sedlim)
		print('flowlim', flowlim)
		count_list = []
		batch_factor = 5

		stepsize_vec = np.empty(self.minlimits_vec.size)
		span = np.abs((self.maxlimits_vec-self.minlimits_vec))
		for i in range(stepsize_vec.size): # calculate the step size of each of the parameters
			stepsize_vec[i] = self.stepratio_vec[i] * span[i]
		print('stepsize_vec:', stepsize_vec)

		# initial values of the parameters to be passed to Blackbox model 
		v_proposal = self.vec_parameters
		v_current = v_proposal # to give initial value of the chain

		reef = Model()





		S_star, cps_star, ca_props_star = self.modelOutputParameters(gt_prop_t,gt_vec_t,gt_timelay)

		if self.lhood_timestructure == True:
			[likelihood, diff, sim_pred_t, sim_pred_d, sim_vec_t, sim_vec_d] = self.likelihoodWithDependence(reef, v_proposal, S_star, cps_star, ca_props_star)
				
		else: # use lhood_depthstructure
			[likelihood, diff, sim_prop_d, sim_vec_d, sim_vec_t] = self.likelihoodWithProps(reef, gt_prop_d, v_proposal)

			#print(likelihood, diff, sim_prop_d, sim_vec_d, sim_vec_t, ' likelihood, diff, sim_prop_d, sim_vec_d, sim_vec_t')





		
		



		print ('\tInitial likelihood:', likelihood, 'and difference score:', diff)
		#---------------------------------------
		# Create memory to save all the accepted proposals of parameters, model predictions and likelihood

		pos_param = np.empty((samples,v_current.size))   
		pos_param[0,:] = v_proposal # assign first proposal

		pos_samples_t = np.empty((samples, sim_vec_t.size)) # list of all accepted (plus repeats) of pred cores  
		pos_samples_t[0,:] = sim_vec_t # assign the first core pred

		pos_samples_d = np.empty((samples, sim_vec_d.size)) # list of all accepted (plus repeats) of pred cores  
		pos_samples_d[0,:] = sim_vec_d # assign the first core pred
		
		pos_likl = np.empty((samples, 2)) # one for posterior of likelihood and the other for all proposed likelihood
		pos_likl[0,:] = [-10000, -10000] # to avoid prob in calc of 5th and 95th percentile later

		# Created to account for asymmetrical proposals 
		p_pr_flow2 = np.empty(self.communities)
		p_pr_flow3 = np.empty(self.communities)
		p_pr_flow4 = np.empty(self.communities)
		p_pr_sed2 = np.empty(self.communities)
		p_pr_sed3 = np.empty(self.communities)
		p_pr_sed4 = np.empty(self.communities)

		#----------------------------------------------------------------------------

		count_list.append(0) # To count number of accepted for each chain (replica)
		accept_list = np.empty(samples)
		start = time.time() 
		num_accepted = 0
		
		with file(('%s/description.txt' % (self.filename)),'a') as outfile:
			outfile.write('\nChain Temp: {0}'.format(self.temperature))
			outfile.write('\n\tSamples: {0}'.format(self.samples))
			outfile.write('\n\tSwap interval: {0}'.format(self.swap_interval))   
			outfile.write('\n\tStepsize vector\n\t{0}'.format(stepsize_vec))  
			outfile.write('\n\tStep ratio vector\n\t{0}'.format(self.stepratio_vec)) 
			outfile.write('\n\tInitial proposed vector\n\t{0}'.format(v_proposal))   

		#---------------------------------------
		
		print('Begin sampling using MCMC random walk')
		#b = 0

		init_count = 0
		
		for i in range(samples-1):


			if i < pt_samples:
				self.adapttemp =  self.temperature #* ratio  #

			if i == pt_samples and init_count ==0: # move to MCMC canonical
				self.adapttemp = 1
				'''if self.lhood_timestructure == True:
					[likelihood_proposal, diff, sim_pred_t, sim_pred_d, sim_vec_t, sim_vec_d] = self.likelihoodWithDependence(reef, v_proposal, S_star, cps_star, ca_props_star)
				else: # use lhood_depthstructure
					[likelihood_proposal, diff, sim_prop_d, sim_vec_d, sim_vec_t] = self.likelihoodWithProps(reef, gt_prop_d, v_proposal)'''
				init_count = 1



			#print (' Sample : ', i, 'accepted ',num_accepted,'Temp           xxxxxxxxxxxxxxxxxxx:',self.temperature)

			idx_sed = int((num_param-3)/2)
			#print(idx_sed, num_param, '  +++++++++++ ' )

			#print(self.realvalues_vec, ' self.realvalues_vec')



			# Update by perturbing all parameters using normal proposal distribution and check limits.


			#init_pro = np.concatenate((sed1,sed2,sed3,sed4,flow1,flow2,flow3,flow4))
			#init_pro = np.append(init_pro,(cm_ax,cm_ay,m)) 


			tmat = v_current[:idx_sed].reshape(4,communities)

			#print(tmat, 'tmat')

			tmatrix = tmat.T
			t2matrix = np.empty((tmatrix.shape[0], tmatrix.shape[1]))
			v_id = 0
			for x in range(communities):
				for s in range(tmat.shape[0]):
					t2matrix[x,s] = self.proposalJump(tmatrix[x,s], self.minlimits_vec[v_id], self.maxlimits_vec[v_id], stepsize_vec[v_id])
					v_id = v_id + 1
			# reorder each row , then transpose back as sed1, etc.
			tmp = np.empty((communities,4))
			for x in range(t2matrix.shape[0]):
				tmp[x,:]  = np.sort(t2matrix[x,:])
				

			tmat = tmp.T
			p_sed1 = tmat[0,:]
			p_sed2 = tmat[1,:]
			p_sed3 = tmat[2,:]
			p_sed4 = tmat[3,:]

			tmat = v_current[idx_sed:2*idx_sed].reshape(4,communities)
			tmatrix = tmat.T
			t2matrix = np.empty((tmatrix.shape[0], tmatrix.shape[1]))
			for x in range(communities):#-3):
				for s in range(tmat.shape[0]):
					t2matrix[x,s] = self.proposalJump(tmatrix[x,s], self.minlimits_vec[v_id], self.maxlimits_vec[v_id], stepsize_vec[v_id])
					v_id = v_id + 1
			
			# reorder each row , then transpose back as flow1, etc.
			tmp = np.empty((communities,4))
			for x in range(t2matrix.shape[0]):
				tmp[x,:] = np.sort(t2matrix[x,:])
				 

			tmat = tmp.T
			p_flow1 = tmat[0,:]
			p_flow2 = tmat[1,:]
			p_flow3 = tmat[2,:]
			p_flow4 = tmat[3,:] 

			p_ax = self.proposalJump(v_current[-3], self.minlimits_vec[v_id], self.maxlimits_vec[v_id], stepsize_vec[v_id])
			v_id = v_id + 1
			p_ay = self.proposalJump(v_current[-2], self.minlimits_vec[v_id], self.maxlimits_vec[v_id], stepsize_vec[v_id])
			v_id = v_id + 1
			p_m = self.proposalJump(v_current[-1], self.minlimits_vec[v_id], self.maxlimits_vec[v_id], stepsize_vec[v_id])

			v_proposal = []
			if (self.sedsim == True) and (self.flowsim == False):
				v_proposal = np.concatenate((p_sed1,p_sed2,p_sed3,p_sed4))
			elif (self.flowsim == True) and (self.sedsim == False):
				v_proposal = np.concatenate((p_flow1,p_flow2,p_flow3,p_flow4))
			elif (self.sedsim == True) and (self.flowsim == True):
				v_proposal = np.concatenate((p_sed1,p_sed2,p_sed3,p_sed4,p_flow1,p_flow2,p_flow3,p_flow4))
			v_proposal = np.append(v_proposal,(p_ax,p_ay,p_m))

			#print(v_proposal, ' v_proposal')

			#print(self.realvalues_vec, ' self.realvalues_vec')


			v_proposal[0:25] = self.realvalues_vec[0:25]

			#print(v_proposal, ' v_proposal ***')


			# print('Sample:', i, ',v_proposal:', v_proposal)
			# Passing paramters to calculate likelihood and diff score

			if self.lhood_timestructure == True:
				[likelihood_proposal, diff, sim_pred_t, sim_pred_d, sim_vec_t, sim_vec_d] = self.likelihoodWithDependence(reef, v_proposal, S_star, cps_star, ca_props_star)

			else: # use lhood_depthstructure
				[likelihood_proposal, diff, sim_prop_d, sim_vec_d, sim_vec_t] = self.likelihoodWithProps(reef, gt_prop_d, v_proposal)

			#del diff 
			#del sim_pred_d
			#del sim_pred_t


			# Difference in likelihood from previous accepted proposal
			diff_likelihood = likelihood_proposal - likelihood
			#print (i, '\tLikelihood proposal:', likelihood_proposal)
			#print('\n\tDifference in likelihood proposals:', diff_likelihood)

			for c in range(communities):
				p_pr_flow2[c] = flowlim[1] - p_flow1[c]
				p_pr_flow3[c] = flowlim[1] - p_flow2[c]
				p_pr_flow4[c] = flowlim[1] - p_flow3[c]
				p_pr_sed2[c] = sedlim[1] - p_sed1[c]
				p_pr_sed3[c] = sedlim[1] - p_sed2[c]
				p_pr_sed4[c] = sedlim[1] - p_sed3[c]
			all_flow_pr = np.array([p_pr_flow2,p_pr_flow3,p_pr_flow4])
			p_pr_flow = np.prod(all_flow_pr)
			all_sed_pr = np.array([p_pr_sed2,p_pr_sed3,p_pr_sed4])
			p_pr_sed = np.prod(all_sed_pr)



			log_pr_flow_p = np.log(p_pr_flow)
			log_pr_sed_p = np.log(p_pr_sed)
			log_pr_flow_c = np.log(c_pr_flow)
			log_pr_sed_c = np.log(c_pr_sed)
			log_pr_p = log_pr_flow_p+log_pr_sed_p
			log_pr_c = log_pr_flow_c+log_pr_sed_c
			log_pr_diff = log_pr_p - log_pr_c

			# print('log_pr_diff', log_pr_diff)

			try:
				mh_prob = min(1, math.exp(diff_likelihood))
			except OverflowError as e:
				mh_prob = 1

			u = random.uniform(0,1)

			#print('u:', u, 'MH probability:', mh_prob)
			#print((i % self.swap_interval), i,  self.swap_interval, 'mod swap')

			pos_likl[i+1,0] = likelihood_proposal
			if u < mh_prob: # Accept sample
				#b = b+1
				print ('Accepted Sample',i, ' \n\tLikelihood ', likelihood_proposal,'\n\tTemperature:', self.temperature,'\n\t  accepted, sample, diff ----------------->:', num_accepted,  diff)
				count_list.append(i)            # Append sample number to accepted list
				num_accepted = num_accepted + 1 
				accept_list[i+1] = num_accepted

				v_current = v_proposal 

				#print(v_proposal)
				likelihood = likelihood_proposal 
				pos_likl[i + 1,1]=likelihood  # contains  all proposal liklihood (accepted and rejected ones) 
				pos_param[i+1,:] = v_current # features rain, erodibility and others  (random walks is only done for this vector) 
				pos_samples_t[i+1,:] =  sim_vec_t # make a list of core predictions
				pos_samples_d[i+1,:] =  sim_vec_d
				c_pr_flow = p_pr_flow
				c_pr_sed = p_pr_sed  

			else: # Reject sample
				#b = i
				pos_likl[i + 1, 1] = pos_likl[i,1]  
				pos_param[i+1,:] = pos_param[i,:] 
				pos_samples_t[i+1,:] = pos_samples_t[i,:] 
				pos_samples_d[i+1,:] = pos_samples_d[i,:] 
 

			
			del idx_sed 
			del tmat 
			del tmatrix 
			del t2matrix 
			del v_id
			del tmp 
				
			
			del p_sed1 
			del p_sed2 
			del p_sed3 
			del p_sed4 
			del p_flow1 
			del p_flow2 
			del p_flow3 
			del p_flow4 
			del  p_ax 
			del p_ay  
			del p_m 
			del v_proposal
			del all_flow_pr 
			del p_pr_flow 
			del all_sed_pr
			del p_pr_sed 
			del log_pr_flow_p 
			del log_pr_sed_p 
			del log_pr_flow_c
			del log_pr_sed_c 
			del log_pr_p 
			del log_pr_c 
			del log_pr_diff
			del u 
			del mh_prob
			
			
			

			#----------------------------------------------------------------------------------------
			if ( i % self.swap_interval == 0 ):  

 
				others = np.asarray([likelihood])
				param = np.concatenate([v_current,others])     


				# paramater placed in queue for swapping between chains
				self.parameter_queue.put(param)

				del param
				del others
				
				
				'''print('-----------------------------------------------')
				print('-----------------------------------------------')
				print (getsizeof(likelihood))
				print('-----------------------------------------------')
				print('-----------------------------------------------')'''

	

				#signal main process to start and start waiting for signal for main
				self.signal_main.set()              
				self.event.wait()
				

				# retrieve parametsrs fom ques if it has been swapped
				if not self.parameter_queue.empty() : 
					try:
						result =  self.parameter_queue.get()
 
						
						v_current= result[0:v_current.size]     
						likelihood = result[v_current.size]

						del result

					except:
						print ('error')	 

					


		accepted_count =  len(count_list) 
		accept_ratio = accepted_count / (samples * 1.0) * 100


		#--------------------------------------------------------------- 

		others = np.asarray([ likelihood])
		param = np.concatenate([v_current,others])   

		self.parameter_queue.put(param)

		file_name = self.filename+'/posterior/pos_likelihood/chain_'+ str(self.temperature)+ '.txt'
		np.savetxt(file_name,pos_likl, fmt='%1.2f')
 
		file_name = self.filename + '/posterior/accept_list/chain_' + str(self.temperature) + '_accept.txt'
		np.savetxt(file_name, [accept_ratio], fmt='%1.2f')

		file_name = self.filename + '/posterior/accept_list/chain_' + str(self.temperature) + '.txt'
		np.savetxt(file_name, accept_list, fmt='%1.2f')
 
		file_name = self.filename+'/posterior/pos_parameters/chain_'+ str(self.temperature)+ '.txt'
		np.savetxt(file_name,pos_param ) 

		file_name = self.filename+'/posterior/predicted_core/pos_samples_t/chain_'+ str(self.temperature)+ '.txt'
		np.savetxt(file_name, pos_samples_t, fmt='%1.2f')

		file_name = self.filename+'/posterior/predicted_core/pos_samples_d/chain_'+ str(self.temperature)+ '.txt'
		np.savetxt(file_name, pos_samples_d, fmt='%1.2f')
 
		del param
		del others
 
		self.signal_main.set()

		return


class ParallelTempering:

	def __init__(self,num_chains,communities, NumSample,fname,xmlinput,num_param,maxtemp,swap_interval,simtime,true_vec_parameters, gt_depths,gt_vec_d,gt_timelay,gt_vec_t,gt_prop_t, gt_prop_d):

		self.num_chains = num_chains
		self.communities = communities
		self.NumSamples = int(NumSample/self.num_chains)
		self.folder = fname
		self.xmlinput = xmlinput
		self.num_param = num_param
		self.maxtemp = maxtemp
		self.swap_interval = swap_interval
		self.simtime = simtime
		self.realvalues  =  true_vec_parameters
		self.gt_depths = gt_depths
		self.gt_vec_d = gt_vec_d
		self.gt_timelay = gt_timelay
		self.gt_vec_t = gt_vec_t
		self.gt_prop_t = gt_prop_t
		self.gt_prop_d = gt_prop_d
		# self.c_pr_flow = c_pr_flow
		# self.c_pr_sed = c_pr_sed
		# self.run_nb_str =run_nb_str 

		self.chains = []
		self.temperature = []
		self.sub_sample_size = max(1, int( 0.05* self.NumSamples))
		self.show_fulluncertainity = False # needed in cases when you reall want to see full prediction of 5th and 95th percentile. Takes more space 

		# Create queues for transfer of parameters between process chain
		self.chain_parameters = [multiprocessing.Queue() for i in range(0, self.num_chains) ]

		self.geometric =  True

		# Two ways events are used to synchronise chains
		self.event = [multiprocessing.Event() for i in range (self.num_chains)]
		self.wait_chain = [multiprocessing.Event() for i in range (self.num_chains)]
 
	# Assign temperature dynamically 


	def default_beta_ladder(self, ndim, ntemps, Tmax): #https://github.com/konqr/ptemcee/blob/master/ptemcee/sampler.py
		"""
		Returns a ladder of :math:`\beta \equiv 1/T` under a geometric spacing that is determined by the
		arguments ``ntemps`` and ``Tmax``.  The temperature selection algorithm works as follows:
		Ideally, ``Tmax`` should be specified such that the tempered posterior looks like the prior at
		this temperature.  If using adaptive parallel tempering, per `arXiv:1501.05823
		<http://arxiv.org/abs/1501.05823>`_, choosing ``Tmax = inf`` is a safe bet, so long as
		``ntemps`` is also specified.
		
		"""

		if type(ndim) != int or ndim < 1:
			raise ValueError('Invalid number of dimensions specified.')
		if ntemps is None and Tmax is None:
			raise ValueError('Must specify one of ``ntemps`` and ``Tmax``.')
		if Tmax is not None and Tmax <= 1:
			raise ValueError('``Tmax`` must be greater than 1.')
		if ntemps is not None and (type(ntemps) != int or ntemps < 1):
			raise ValueError('Invalid number of temperatures specified.')

		tstep = np.array([25.2741, 7., 4.47502, 3.5236, 3.0232,
						2.71225, 2.49879, 2.34226, 2.22198, 2.12628,
						2.04807, 1.98276, 1.92728, 1.87946, 1.83774,
						1.80096, 1.76826, 1.73895, 1.7125, 1.68849,
						1.66657, 1.64647, 1.62795, 1.61083, 1.59494,
						1.58014, 1.56632, 1.55338, 1.54123, 1.5298,
						1.51901, 1.50881, 1.49916, 1.49, 1.4813,
						1.47302, 1.46512, 1.45759, 1.45039, 1.4435,
						1.4369, 1.43056, 1.42448, 1.41864, 1.41302,
						1.40761, 1.40239, 1.39736, 1.3925, 1.38781,
						1.38327, 1.37888, 1.37463, 1.37051, 1.36652,
						1.36265, 1.35889, 1.35524, 1.3517, 1.34825,
						1.3449, 1.34164, 1.33847, 1.33538, 1.33236,
						1.32943, 1.32656, 1.32377, 1.32104, 1.31838,
						1.31578, 1.31325, 1.31076, 1.30834, 1.30596,
						1.30364, 1.30137, 1.29915, 1.29697, 1.29484,
						1.29275, 1.29071, 1.2887, 1.28673, 1.2848,
						1.28291, 1.28106, 1.27923, 1.27745, 1.27569,
						1.27397, 1.27227, 1.27061, 1.26898, 1.26737,
						1.26579, 1.26424, 1.26271, 1.26121,
						1.25973])

		if ndim > tstep.shape[0]:
			# An approximation to the temperature step at large
			# dimension
			tstep = 1.0 + 2.0*np.sqrt(np.log(4.0))/np.sqrt(ndim)
		else:
			tstep = tstep[ndim-1]

		appendInf = False
		if Tmax == np.inf:
			appendInf = True
			Tmax = None
			ntemps = ntemps - 1

		if ntemps is not None:
			if Tmax is None:
				# Determine Tmax from ntemps.
				Tmax = tstep ** (ntemps - 1)
		else:
			if Tmax is None:
				raise ValueError('Must specify at least one of ``ntemps'' and '
								'finite ``Tmax``.')

			# Determine ntemps from Tmax.
			ntemps = int(np.log(Tmax) / np.log(tstep) + 2)

		betas = np.logspace(0, -np.log10(Tmax), ntemps)
		if appendInf:
			# Use a geometric spacing, but replace the top-most temperature with
			# infinity.
			betas = np.concatenate((betas, [0]))

		return betas
		
		
	def assign_temperatures(self):
		# #Linear Spacing
		# temp = 2
		# for i in range(0,self.num_chains):
		#   self.temperatures.append(temp)
		#   temp += 2.5 #(self.maxtemp/self.num_chains)
		#   print (self.temperatures[i])
		#Geometric Spacing

		if self.geometric == True:
			betas = self.default_beta_ladder(2, ntemps=self.num_chains, Tmax=self.maxtemp)      
			for i in range(0, self.num_chains):         
				self.temperature.append(np.inf if betas[i] is 0 else 1.0/betas[i])
				print (self.temperature[i])
		else:

			tmpr_rate = (self.maxtemp /self.num_chains)
			temp = 1
			print("Temperatures...")
			for i in xrange(0, self.num_chains):            
				self.temperatures.append(temp)
				temp += tmpr_rate
				print(self.temperature[i])

	'''def assign_temptarures(self):
		tmpr_rate = (self.maxtemp /self.num_chains)
		temp = 1
		for i in xrange(0, self.num_chains):            
			self.temperature.append(temp)
			temp += tmpr_rate
			print('self.temperature[%s]' % i,self.temperature[i])'''
			 
	def initialise_chains (self, vis, num_communities, num_sed, num_flow, sedlim, flowlim, maxlimits_vec, minlimits_vec , stepratio_vec,  choose_likelihood,   burn_in):
		self.burn_in = burn_in

		self.assign_temperatures()
		for i in xrange(0, self.num_chains):
			vec_parameters, c_pr_flow, c_pr_sed = initial_vec(num_communities, num_sed, num_flow, sedlim, flowlim, minlimits_vec[-2], maxlimits_vec[-2], minlimits_vec[-1], maxlimits_vec[-1])
			self.chains.append(ptReplica(self.NumSamples,self.folder,self.xmlinput, vis, num_communities, vec_parameters, self.realvalues, maxlimits_vec, minlimits_vec, stepratio_vec, 
				choose_likelihood, self.swap_interval, self.simtime, c_pr_flow, c_pr_sed, self.gt_depths, self.gt_vec_d, self.gt_timelay, self.gt_vec_t, self.gt_prop_t, self.gt_prop_d,
				self.temperature[i], self.chain_parameters[i], self.event[i], self.wait_chain[i],burn_in))
	
	def run_chains (self):
		
		# only adjacent chains can be swapped therefore, the number of proposals is ONE less num_chains
		swap_proposal = np.ones(self.num_chains-1) 
		
		# create parameter holders for paramaters that will be swapped
		replica_param = np.zeros((self.num_chains, self.num_param))  
		lhood = np.zeros(self.num_chains)

		# Define the starting and ending of MCMC Chains
		start = 0
		end = self.NumSamples-1

		number_exchange = np.zeros(self.num_chains)

		# filen = open(self.folder + '/num_exchange.txt', 'a')
		
		#-------------------------------------------------------------------------------------
		# run the MCMC chains
		#-------------------------------------------------------------------------------------
		for l in range(0,self.num_chains):
			self.chains[l].start_chain = start
			self.chains[l].end = end
		
		#-------------------------------------------------------------------------------------
		# run the MCMC chains
		#-------------------------------------------------------------------------------------
		for j in range(0,self.num_chains):        
			self.chains[j].start()

		flag_running = True 

		
		while flag_running:          

			#-------------------------------------------------------------------------------------
			# wait for chains to complete one pass through the samples
			#-------------------------------------------------------------------------------------

			for j in range(0,self.num_chains): 
				#print (j, ' - waiting')
				self.wait_chain[j].wait()
			

			
			#-------------------------------------------------------------------------------------
			#get info from chains
			#-------------------------------------------------------------------------------------
			
			for j in range(0,self.num_chains): 
				if self.chain_parameters[j].empty() is False :
					result =  self.chain_parameters[j].get()
					replica_param[j,:] = result[0:self.num_param]   
					lhood[j] = result[self.num_param]

					del result
 
 

			# create swapping proposals between adjacent chains
			for k in range(0, self.num_chains-1): 
				swap_proposal[k]=  (lhood[k]/[1 if lhood[k+1] == 0 else lhood[k+1]])*(1/self.temperature[k] * 1/self.temperature[k+1])

			#print(' before  swap_proposal  --------------------------------------+++++++++++++++++++++++=-')

			for l in range( self.num_chains-1, 0, -1):
				#u = 1
				u = random.uniform(0, 1)
				swap_prob = swap_proposal[l-1]



				if u < swap_prob : 

					number_exchange[l] = number_exchange[l] +1  

					others = np.asarray([  lhood[l-1] ]  ) 
					para = np.concatenate([replica_param[l-1,:],others])   
 
				   
					self.chain_parameters[l].put(para) 

					others = np.asarray([ lhood[l] ] )
					param = np.concatenate([replica_param[l,:],others])
 
					self.chain_parameters[l-1].put(param)

					del para
					del others
					del param
					
				else:


					others = np.asarray([  lhood[l-1] ])
					para = np.concatenate([replica_param[l-1,:],others]) 
 
					self.chain_parameters[l-1].put(para) 

					others = np.asarray([  lhood[l]  ])
					param = np.concatenate([replica_param[l,:],others])
 
					self.chain_parameters[l].put(param)

					del para
					del others
					del param

				del u
				del swap_prob



			#-------------------------------------------------------------------------------------
			# resume suspended process
			#-------------------------------------------------------------------------------------
			for k in range (self.num_chains):
					self.event[k].set()

								

			#-------------------------------------------------------------------------------------
			#check if all chains have completed runing
			#-------------------------------------------------------------------------------------
			count = 0
			for i in range(self.num_chains):
				if self.chains[i].is_alive() is False:
					count+=1
					while self.chain_parameters[i].empty() is False:
						dummy = self.chain_parameters[i].get()
						del dummy

			if count == self.num_chains :
				flag_running = False
			
			del count

			
			gc.collect() # fLet the main threag constantly be removing files from memory


		#-------------------------------------------------------------------------------------
		#wait for all processes to jin the main process
		#-------------------------------------------------------------------------------------     
		for j in range(0,self.num_chains): 
			self.chains[j].join()

		print('Process ended.\n\tNo. exchange:', number_exchange)

		burnin, pos_param, likelihood_rep, accept_list,  accept, list_predcore_t, list_predcore_d = self.show_results('chain_')		
		
		self.summ_stats(self.folder, pos_param)

		optimal_likl, optimal_para, para_5pcent, para_95pcent = self.get_optimal(likelihood_rep, pos_param)
		print('optimal_likl', optimal_likl)

		outfile = open(self.folder+'/optimal_percentile_para.txt', 'a+')
		hdr = np.array(['optimal_para', 'para_5pcent', 'para_95pcent'])
		np.savetxt(outfile, hdr, fmt="%s", delimiter=' ')
		np.savetxt(outfile, [optimal_para,para_5pcent,para_95pcent],fmt='%1.2ff',delimiter=' ')
		np.savetxt(outfile,['optimal likelihood'], fmt='%s')
		np.savetxt(outfile,[optimal_likl], fmt='%1.2f')
		# np.savetxt(outfile, [np.array(['Optimal Parameters']),optimal_para], fmt='%1.2f',delimiter=' ')
		# np.savetxt(outfile,[np.array(['para_5pcent']), para_5pcent], fmt='%1.2f',delimiter=' ')
		# np.savetxt(outfile,[np.array(['para_95pcent']), para_95pcent], fmt='%1.2f',delimiter=' ')
		x_tick_labels = ['Shallow', 'Mod-deep', 'Deep', 'Sediment','No growth']
		x_tick_values = [1,2,3,4,5]
		plotResults.plotPosCore(self.folder,list_predcore_d.T,list_predcore_t.T, self.gt_vec_d, self.gt_vec_t, self.gt_depths, self.gt_timelay, x_tick_labels,x_tick_values, 9)
		plotResults.boxPlots(self.communities, pos_param, True, True, 9, 1, self.folder)

		sample_range = np.arange(burnin+1,self.NumSamples+1, 1)
		'''for s in range(self.num_param):  
			self.plot_figure(pos_param[s,:], 'pos_distri_'+str(s), self.realvalues[s], sample_range) 
		'''
		return (pos_param,likelihood_rep, accept_list,  list_predcore_t, list_predcore_d)

	# Merge different MCMC chains y stacking them on top of each other
	def show_results(self, filename):

		burnin = int(self.NumSamples * self.burn_in)
		print('Burnin:',burnin)

		
		#file_name = self.folder + '/posterior/pos_parameters/'+filename + str(self.temperature[0]) + '.txt'
		#dat_dummy = np.loadtxt(file_name) 

		#print(dat_dummy.shape)

		pos_param = np.zeros((self.num_chains, self.NumSamples -burnin, self.num_param))
		print('Pos_param.shape:', pos_param.shape)
		pred_t = np.zeros((self.num_chains, self.NumSamples-burnin, self.gt_vec_t.shape[0]))
		pred_d = np.zeros((self.num_chains, self.NumSamples-burnin, self.gt_vec_d.shape[0]))

		print('pred_t:',pred_t,'pred_t.shape:', pred_t.shape)
		print('gt_prop_t.shape:',self.gt_prop_t.shape)

		file_name = self.folder + '/posterior/pos_likelihood/'+filename + str(self.temperature[0]) + '.txt'
		dat = np.loadtxt(file_name) 
		likelihood_rep = np.zeros((self.num_chains, dat.shape[0]-burnin, 2 )) # index 1 for likelihood posterior and index 0 for Likelihood proposals. Note all likilihood proposals plotted only
		
		'''
		pos_param = np.zeros((self.num_chains, self.NumSamples - burnin , self.num_param))
		print('Pos_param.shape:', pos_param.shape)
		pred_t = np.zeros((self.num_chains, self.NumSamples - burnin, self.gt_vec_t.shape[0]))
		pred_d = np.zeros((self.num_chains, self.NumSamples - burnin, self.gt_vec_d.shape[0]))
		print('pred_t:',pred_t,'pred_t.shape:', pred_t.shape)
		print('gt_prop_t.shape:',self.gt_prop_t.shape)
		
 
		likelihood_rep = np.zeros((self.num_chains, self.NumSamples - burnin, 2 )) # index 1 for likelihood posterior and index 0 for Likelihood proposals. Note all likilihood proposals plotted only
		'''

		accept_percent = np.zeros((self.num_chains, 1))

		accept_list = np.zeros((self.num_chains, self.NumSamples )) 
 
		for i in range(self.num_chains):
			file_name = self.folder + '/posterior/pos_parameters/'+filename + str(self.temperature[i]) + '.txt'
			dat = np.loadtxt(file_name) 
			print('dat.shape:',dat.shape) 
			pos_param[i, :, :] = dat[burnin:,:]
			
			file_name = self.folder + '/posterior/predicted_core/pos_samples_t/chain_'+  str(self.temperature[i]) + '.txt'
			dat = np.loadtxt(file_name)
			pred_t[i, :, :] = dat[burnin:,:] 

			file_name = self.folder + '/posterior/predicted_core/pos_samples_d/chain_'+  str(self.temperature[i]) + '.txt'
			dat = np.loadtxt(file_name)
			pred_d[i, :, :] = dat[burnin:,:] 

			file_name = self.folder + '/posterior/pos_likelihood/'+filename + str(self.temperature[i]) + '.txt'
			dat = np.loadtxt(file_name) 
			likelihood_rep[i, :] = dat[burnin:]

			file_name = self.folder + '/posterior/accept_list/' + filename + str(self.temperature[i]) + '.txt'
			dat = np.loadtxt(file_name) 
			accept_list[i, :] = dat 

			file_name = self.folder + '/posterior/accept_list/' + filename + str(self.temperature[i]) + '_accept.txt'
			dat = np.loadtxt(file_name) 
			accept_percent[i, :] = dat

		likelihood_vec = likelihood_rep.transpose(2,0,1).reshape(2,-1) 
		posterior = pos_param.transpose(2,0,1).reshape(self.num_param,-1)
		list_predcore_t = pred_t.transpose(2,0,1).reshape(self.gt_vec_t.shape[0],-1)
		list_predcore_d = pred_d.transpose(2,0,1).reshape(self.gt_vec_d.shape[0],-1)

		# list_predcore = pred_core.transpose(2,0,1).reshape(self.gt_prop_t.shape[0],-1)
		accept = np.sum(accept_percent)/self.num_chains

		np.savetxt(self.folder + '/pos_param.txt', posterior.T) 
		
		np.savetxt(self.folder + '/likelihood.txt', likelihood_vec.T, fmt='%1.5f')

		np.savetxt(self.folder + '/accept_list.txt', accept_list, fmt='%1.2f')
  
		np.savetxt(self.folder + '/acceptpercent.txt', [accept], fmt='%1.2f')

		return burnin, posterior, likelihood_vec.T, accept_list, accept, list_predcore_t, list_predcore_d

	def find_nearest(self, array,value): 
		# Find nearest index for a particular value
		idx = (np.abs(array-value)).argmin()
		return array[idx], idx

	def get_optimal(self, likelihood_rep, pos_param): 

		likelihood_pos = likelihood_rep[:,1]
		
		# Find 5th and 95th percentile of a posterior
		a = np.percentile(likelihood_pos, 5)   
		# Find nearest value of 5th/95th percentiles in posterior likelihood 
		lhood_5thpercentile, index_5th = self.find_nearest(likelihood_pos,a)  
		b = np.percentile(likelihood_pos, 95) 
		lhood_95thpercentile, index_95th = self.find_nearest(likelihood_pos,b)  

		# Find max of pos liklihood to get the max or optimal posterior value  
		max_index = np.argmax(likelihood_pos) 
		optimal_likelihood = likelihood_pos[max_index]	
		optimal_para = pos_param[:, max_index] 
		
		para_5thperc = pos_param[:, index_5th]
		para_95thperc = pos_param[:, index_95th] 

		return optimal_likelihood, optimal_para, para_5thperc, para_95thperc

	def summ_stats(self, fname, pos_param):
		print
		# Find mean, mode, mode count, st. dev, min, max, 5th, 95th, standard error of the mean
		
		summ_stats = np.zeros((pos_param.shape[0],9))
		print('summ stats shape', summ_stats.shape)
		
		# outfile = open(fname+'/summ_stats.txt','a+')
		# data = ['Mu', 'Mode', 'Mode cnt','Sigma','Min','Max','5th pcentile', '95th pcentile', 'SEM']
		# np.savetxt(outfile, data, newline='\n')

		# Write header of summary statistics
		with file(fname + '/summ_stats.csv','wb') as outfile:
			writer = csv.writer(outfile, delimiter='\t')
			data = ['Parameter','Mu', 'Mode', 'Mode(count)','Sigma','Min','Max','5percentile', '95percentile', 'SEM']
			writer.writerow(data)

		for i in range(pos_param.shape[0]):
			summ_stats[i,0] = np.mean(pos_param[:,i])
			pmode =  stats.mode(pos_param[:,i])
			summ_stats[i,1] = float(pmode[0])
			summ_stats[i,2] = float(pmode[1])
			summ_stats[i,3] = np.std(pos_param[:,i])
			summ_stats[i,4] = np.min(pos_param[:,i])
			summ_stats[i,5] = np.max(pos_param[:,i])
			summ_stats[i,6] = np.percentile(pos_param[:,i], 5)
			summ_stats[i,7] = np.percentile(pos_param[:,i], 95)
			summ_stats[i,8] = stats.sem(pos_param[:,i], axis=None) 

			# np.savetxt(outfile, summ_stats[i,:], newline='\n')
			with file(fname + '/summ_stats.csv', 'ab') as outfile:
				writer = csv.writer(outfile, delimiter='\t')
				data = [np.ndarray.tolist(summ_stats[i,:])]
				writer.writerow([i,data])

	def plot_figure(self, list, title, real_value, sample_range): 

		list_points =  list

		fname = self.folder
		width = 1 
		font = 12

		slen = np.arange(0,len(list),1) 
		 
		fig = plt.figure(figsize=(8, 9))
		ax = fig.add_subplot(111)
		# ax.tick_params(labelsize=25)
		params = {'ytick.labelsize': 'large','xtick.labelsize': 'large'}
		plt.rcParams.update(params)
		ax.spines['top'].set_color('none')
		ax.spines['bottom'].set_color('none')
		ax.spines['left'].set_color('none')
		ax.spines['right'].set_color('none')
		ax.tick_params(labelcolor='w', top=False, bottom=False, left=False, right=False)
		ax.set_title(' Posterior distribution', fontsize=  font+2)#, y=1.02)
	
		ax1 = fig.add_subplot(211) 

		n, rainbins, patches = ax1.hist(list_points,  bins = 20,  alpha=0.5, facecolor='sandybrown', normed=False)	
 
   
		ax1.axvline(x=real_value, color='blue', linestyle='dashed', linewidth=1) # comment when go real value is 

		print('real_value:',real_value)

		ax1.grid(True)
		ax1.set_ylabel('Frequency',size= font+1)
		ax1.set_xlabel('Parameter values', size= font+1)
	
		ax2 = fig.add_subplot(212)

		list_points = np.asarray(np.split(list_points,  self.num_chains ))

		print("------------------------------------------------------------")
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")
		print( sample_range)
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")
		print (list_points.T)
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")

		print('sample_range shape', sample_range.shape)

		print('list_points.T shape', list_points.T.shape)
 
		print("------------------------------------------------------------")
		print("------------------------------------------------------------")


		ax2.set_facecolor('#f2f2f3') 
		ax2.plot(sample_range, list_points.T , label=None)
		ax2.set_title(r'Trace plot',size= font+2)
		ax2.set_xlabel('Samples',size= font+1)
		ax2.set_ylabel('Parameter values', size= font+1) 
		ax2.set_xlim([np.amin(sample_range),np.amax(sample_range)])
		fig.tight_layout()
		fig.subplots_adjust(top=0.88)
		 
 
		plt.savefig(fname + '/plot_pos/' + title  + '.png', bbox_inches='tight', dpi=300, transparent=False)
		plt.close()
 

def mean_sqerror(  pred_erodep, pred_elev,  real_elev,  real_erodep_pts):
		 
		elev = np.sqrt(np.sum(np.square(pred_elev -  real_elev))  / real_elev.size)  
		sed =  np.sqrt(  np.sum(np.square(pred_erodep -  real_erodep_pts)) / real_erodep_pts.size  ) 

		return elev + sed, sed

def find_limits(communities, num_sed, num_flow, sedlim, flowlim,  min_a, max_a, min_m, max_m):

	sedmax_vec =  np.repeat(sedlim[1], communities * num_sed )  # vec size =12
	sedmin_vec =  np.repeat(sedlim[0], communities * num_sed ) 

	flowmax_vec =  np.repeat(flowlim[1], communities * num_flow )  
	flowmin_vec =  np.repeat(flowlim[0], communities * num_flow) 
 
	glv_max = np.array([  max_a, max_a, max_m]) 
	glv_min = np.array([  min_a, min_a, min_m])

	maxlimits_vec = np.concatenate((sedmax_vec , flowmax_vec, glv_max))
	minlimits_vec = np.concatenate((sedmin_vec , flowmin_vec, glv_min))


	return   maxlimits_vec, minlimits_vec

def initial_vec(communities, num_sed, num_flow, sedlim, flowlim, min_a, max_a, min_m, max_m):
	print('communities',communities)
	print('num_sed',num_sed)
	print('num_flow',num_flow)
	print('sedlim',sedlim,'flowlim',flowlim,'min_a',min_a, 'max a',max_a,'min m',min_m,'max m', max_m)

	sed1 = np.empty(communities)
	sed2 = np.empty(communities)
	sed3 = np.empty(communities)
	sed4 = np.empty(communities)
	pr_flow2 = np.empty(communities)
	pr_flow3 = np.empty(communities)
	pr_flow4 = np.empty(communities)
	pr_sed2 = np.empty(communities)
	pr_sed3 = np.empty(communities)
	pr_sed4 = np.empty(communities)
 
	for s in range(communities):

		sed1[s] = np.random.uniform(sedlim[0],sedlim[1])
		sed2[s] = np.random.uniform(sed1[s],sedlim[1])
		sed3[s] = np.random.uniform(sed2[s],sedlim[1])
		sed4[s] = np.random.uniform(sed3[s],sedlim[1])

	flow1 = np.empty(communities)
	flow2 = np.empty(communities)
	flow3 = np.empty(communities)
	flow4 = np.empty(communities) 
			
	for x in range(communities):
		flow1[x] = np.random.uniform(flowlim[0], flowlim[1])
		flow2[x] = np.random.uniform(flow1[x], flowlim[1])
		flow3[x] = np.random.uniform(flow2[x], flowlim[1])
		flow4[x] = np.random.uniform(flow3[x], flowlim[1])
		
	cm_ax = np.random.uniform(min_a,max_a)
	cm_ay = np.random.uniform(min_a,max_a)
	m = np.random.uniform(min_m,max_m)
	# # If fixing parameters
	# maxlimits_vec[24] = true_vec_parameters[24]
	# maxlimits_vec[25] = true_vec_parameters[25]
	# vec_parameters = true_vec_parameters

	for c in range(communities):
		pr_flow2[c] = flowlim[1] - flow1[c]
		pr_flow3[c] = flowlim[1] - flow2[c]
		pr_flow4[c] = flowlim[1] - flow3[c]
		pr_sed2[c] = sedlim[1] - sed1[c]
		pr_sed3[c] = sedlim[1] - sed2[c]
		pr_sed4[c] = sedlim[1] - sed3[c]
	prs_flow = np.array([pr_flow2,pr_flow3,pr_flow4])
	c_pr_flow = np.prod(prs_flow)
	prs_sed = np.array([pr_sed2,pr_sed3,pr_sed4])
	c_pr_sed = np.prod(prs_sed)

	init_pro = np.concatenate((sed1,sed2,sed3,sed4,flow1,flow2,flow3,flow4))
	init_pro = np.append(init_pro,(cm_ax,cm_ay,m)) 

	print('Initial parameters:', init_pro) 

	return init_pro, c_pr_flow, c_pr_sed

def make_directory (directory): 
	if not os.path.exists(directory):
		os.makedirs(directory)
 

def main():

	random.seed(time.time()) 


	if(len(sys.argv)!=5):
		sys.exit('not right input format.  ')



	problem = int(sys.argv[1])  # get input

	num_chains = int(sys.argv[2])

	swap_interval = int(sys.argv[3])

	samples = int(sys.argv[4])

	print (problem, num_chains,   swap_interval)

	#-------------------------------------------------------------------------------------
	# Number of chains of MCMC required to be run
	# PT is a multicore implementation must num_chains >= 2
	# Choose a value less than the numbe of core available (avoid context swtiching)
	#-------------------------------------------------------------------------------------
	#samples = 5000    # total number of samples by all the chains (replicas) in parallel tempering
	#num_chains = 10 # number of Replica's that will run on separate cores. Note that cores will be shared automatically - if enough cores not available
	  
	burn_in = 0.2  


	


	#parameters for Parallel Tempering
	maxtemp = 5 
	
	 

	choose_likelihood = 1 # 1 for Multinomial, 2 for Gaussian Likilihood
	# problem = input("Which problem do you want to choose? \n\t1. Testing (Synthetic, 2. Heron  3. OneTreeReef")
	
	if problem == 1:
		num_communities = 3 # can be 6 for real probs
		num_flow = 4
		num_sed = 4
		simtime = 8500 
		sedlim = [0., 0.005]
		flowlim = [0.,0.3]
		min_a = -0.15 # Community interaction matrix diagonal and sub-diagnoal limits
		max_a = 0.
		min_m = 0.
		max_m = 0.15 # Malthusian parameter limit

		maxlimits_vec, minlimits_vec = find_limits(num_communities, num_sed, num_flow, sedlim, flowlim, min_a, max_a, min_m, max_m)

		stepsize_ratio  = 0.05 #   you can have different ratio values for different parameters depending on the problem. Its safe to use one value for now
		stepratio_vec =  np.repeat(stepsize_ratio, maxlimits_vec.size) 
		num_param = maxlimits_vec.size 

		problemfolder = 'SyntheticProblem/'  # change for other reef-core (This is synthetic core) 

		true_vec_parameters = np.loadtxt(problemfolder +'data_new/core_3asemb/true_values.txt')


		#synthdata_depthdata_3assem



		'''gt_depths, gt_vec_d = np.genfromtxt(problemfolder +'data/synthdata_d_vec.txt', usecols=(0,1), unpack=True)
		synth_data = problemfolder +'data/synthdata_t_prop.txt'
		gt_prop_t = np.loadtxt(synth_data, usecols=(1,2,3,4,5)) 
		synth_vec = problemfolder +'data/synthdata_t_vec.txt'
		gt_timelay, gt_vec_t = np.genfromtxt(synth_vec, usecols=(0, 1), unpack = True)
		gt_timelay = gt_timelay[::-1]'''

	else:
		print('Choose a problem.\n\t1. Testing (Synthetic), 2. Heron Reef, 3. One Tree Reef')




	xmlinput = problemfolder + 'input_synth.xml' 

	coredata_depthprop = problemfolder +'data_new/core_3asemb/synth_core_prop_d_08.txt' # depth structure  propotions

	coredata_depthid = problemfolder +'data_new/core_3asemb/synth_core_vec_d_08.txt' # depth structure id

	coredata_timeid = problemfolder +'data_new/core_3asemb/synthdata_t_vec_08_1.txt' # time structure id

	coredata_timeprop = problemfolder +'data_new/core_3asemb/synthdata_t_prop_08_1.txt' # time structure propotions

	

	gt_depths, gt_vec_d = np.genfromtxt(coredata_depthid, usecols=(0,1), unpack=True)
 
	gt_timelay, gt_vec_t = np.genfromtxt(coredata_timeid, usecols=(0, 1), unpack = True)
	gt_prop_d = np.loadtxt(coredata_depthprop, usecols=(1,2,3,4))
	gt_prop_t = np.loadtxt(coredata_timeprop, usecols=(1,2,3,4))
	gt_timelay = gt_timelay[::-1]

	
	print(gt_depths, ' gt depths')
	print(gt_vec_d, ' gt_vec_d') 

	print(gt_timelay, ' gt_timelay') 
	print(gt_vec_t, ' gt_vec_t')

	print(gt_prop_d, ' gt_prop_d')




	# datafile = problemfolder + 'data/synth_core_vec.txt'
	# core_depths, data_vec = np.genfromtxt(datafile, usecols=(0, 1), unpack = True) 
	# core_data = np.loadtxt(problemfolder + 'data/synth_core_prop.txt', usecols=(1,2,3,4))

	vis = [False, False] # first for initialisation, second for cores
	sedsim, flowsim = True, True  # can pass this to pt class later

	fname = ""
	run_nb = 0
	while os.path.exists(problemfolder +'results_%s' % (run_nb)):
		run_nb += 1
	if not os.path.exists(problemfolder +'results_%s' % (run_nb)):
		os.makedirs(problemfolder +'results_%s' % (run_nb))
		fname = (problemfolder +'results_%s' % (run_nb))
 
	make_directory((fname + '/posterior/pos_parameters')) 
	make_directory((fname + '/posterior/predicted_core/pos_samples_t'))
	make_directory((fname + '/posterior/predicted_core/pos_samples_d'))
	make_directory((fname + '/posterior/pos_likelihood'))
	make_directory((fname + '/posterior/accept_list')) 

	make_directory((fname + '/plot_pos'))

	run_nb_str = 'results_' + str(run_nb)

	#-------------------------------------------------------------------------------------
	#Create A a Patratellel Tempering object instance 
	#-------------------------------------------------------------------------------------
	timer_start = time.time()


	#synthdata_depthdata_3assem

	pt = ParallelTempering(num_chains,num_communities, samples,fname,xmlinput,num_param,maxtemp,swap_interval,simtime, true_vec_parameters,gt_depths, gt_vec_d, gt_timelay, gt_vec_t, gt_prop_t, gt_prop_d)
	 
	pt.initialise_chains(vis, num_communities, num_sed, num_flow, sedlim, flowlim, maxlimits_vec, minlimits_vec , stepratio_vec, choose_likelihood, burn_in)
	#-------------------------------------------------------------------------------------
	#run the chains in a sequence in ascending order
	#-------------------------------------------------------------------------------------
	pos_param,likelihood_rep, accept_list, pred_core_t, pred_core_d = pt.run_chains()
	print('Pred core:',pred_core_t)

	print('Successfully sampled') 

	timer_end = time.time() 
	likelihood = likelihood_rep[:,0] # just plot proposed likelihood  
	likelihood = np.asarray(np.split(likelihood,  num_chains ))
	
	s_range = np.arange(int((burn_in * samples)/num_chains),(samples/num_chains)+1, 1)
	sample_range = np.zeros((len(s_range), num_chains))
	# sample_range = np.zeros((num_chains,len(s_range)))
	for i in range(num_chains):
		sample_range[:,i] = s_range
	# sample_range = np.arange(int((burn_in * samples)/num_chains)+1,samples+1, 1)
	
	font=12
	fig = plt.figure(figsize=(8,6))
	#plt.plot(sample_range[1:,:], likelihood.T)

	plt.plot( likelihood.T)
	plt.title('Likelihood Evolution')
	plt.xlabel('Likelihood')
	plt.ylabel('Samples')
	#plt.xlim(np.amin(sample_range[1:,:]),np.amax(sample_range))
	plt.savefig( fname+'/likelihood.png')
	plt.clf()


	#adj_acceptlist = accept_list +1
	plt.plot( accept_list.T)
	plt.title('Acceptance through time', size=font)
	plt.xlabel('Number of samples accepted', size=font)
	plt.ylabel('Samples', size=font)
	#plt.xlim(np.amin(sample_range),np.amax(sample_range))
	plt.savefig( fname+'/accept_list.png')
	plt.clf()
 
	
	print ('Time taken  in minutes = ', (timer_end-timer_start)/60)
	np.savetxt(fname+'/time_sqerror.txt',[ (timer_end-timer_start)/60], fmt='%1.2f'  )

	mpl_fig = plt.figure()
	ax = mpl_fig.add_subplot(111)

	ax.boxplot(pos_param.T) 
	plt.title("Boxplot of Posterior", size=font)
	ax.set_xlabel('pt-BayesReef parameters', size=font)
	ax.set_ylabel('Parameter values', size=font) 
	plt.legend(loc='upper right') 
	plt.savefig(fname+'/pt-BayesReef_pos.png') 
	plt.clf()

	#stop()
if __name__ == "__main__": main() 