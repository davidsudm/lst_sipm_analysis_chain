#!/usr/bin/env python
"""
A simple script for gamma rate calculation from merged DL1 data (integrated waveforms, Hillas parameters,...)
Usage:
  ./gamma_rate.py --output filename
"""

import argparse
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import scipy.stats as stats
from ctapipe.io import HDF5TableReader
from lstchain.io.lstcontainers import ThrownEventsHistogram
from lstchain.io.io import read_simtel_energy_histogram, read_simu_info_hdf5
from lstchain.io import read_configuration_file, standard_config, replace_config
from lstchain.reco import utils
from scipy.integrate import quad


dl1_params_lstcam_key = 'dl1/event/telescope/parameters/LST_LSTCam'

parser = argparse.ArgumentParser()

parser.add_argument('filename')

parser.add_argument('--config_file', '-conf', action='store', type=str,
                    dest='config_file',
                    help='Path to a configuration file. If none is given, a standard configuration is applied',
                    default=None
                    )

parser.add_argument('--output', action='store', type=str,
                    dest='output',
                    default=None
                    )

parser.add_argument('--telescope', '-t', action='store', type=int,
                    dest='telescope',
                    default=1
                    )

parser.add_argument('--cam_key', action='store', type=str,
                    dest='dl1_params_camera_key',
                    default=dl1_params_lstcam_key
                    )

args = parser.parse_args()

def read_simtel_energy_histogram_merged(filename):
    """
    Read the simtel energy histogram from merged HDF5 file.
    """
    with HDF5TableReader(filename=filename) as reader:
        histtab = reader.read('/simulation/thrown_event_distribution', ThrownEventsHistogram())
        hh = ThrownEventsHistogram()
        for i in range(0, 100000):
            try:
                hist = next(histtab)
                if i == 0:
                    hh.histogram = hist.histogram
                else:
                    hh.histogram = hist.histogram + hh.histogram
                #print(hist.obs_id, i)
            except StopIteration:
                print(hist.num_entries * i, np.sum(np.sum(hh.histogram, axis=1)))
                break;
    return hist, hh

def efficiency(all_hist, param_hist, energy_bins_edges):

    e_centers = (energy_bins_edges[:-1] + energy_bins_edges[1:])/2.0
    P = param_hist/all_hist
    P[all_hist == 0] = 0
    return e_centers,P

def crab_hegra_dNdE(energy):
    const = 2.83*10**-7 # TeV^-1 m^-2 s^-1
    spectral_index_real = -2.62
    e0 = 1 # TeV
    spectrum = const * (energy / e0)**spectral_index_real
    return spectrum


if __name__ == '__main__':

    filename=args.filename
    #'/nfs/cta-ifae/jurysek/mc_DL1/20190415/gamma/south_pointing/20190923/dl1_20190415_gamma_south_pointing_20190323_training-noimage.h5'

    custom_config = {}
    if args.config_file is not None:
        try:
            custom_config = read_configuration_file(args.config_file)
        except("Custom configuration could not be loaded !!!"):
            pass

    config = replace_config(standard_config, custom_config)
    events_filters = config["events_filters"]

    # nacteni pouze sloupcu s parametry
    param = pd.read_hdf(filename, key=args.dl1_params_camera_key)
    param = utils.filter_events(param, filters=events_filters)

    # zakladni statistika dat
    #print(param.describe())
    print(param.columns)
    print(len(param))

    # energy histogram (thrown events)
    # - kazdy sloupec matice histogram je jeden bin v core distance
    # - kazdy radek je jeden bin v energii
    # - pro simulovane spektrum energii se musi poscitat vsechny sloupce - axis=1
    #hist = read_simtel_energy_histogram(filename)
    hist, hist_merged = read_simtel_energy_histogram_merged(filename)
    #print(hist)
    print('E_min [Tev]: {:.4f}, E_max [TeV]: {:.4f}, N_bins: {:d}'.format(min(hist.bins_energy), max(hist.bins_energy), len(hist.bins_energy)-1))

    mc_header = read_simu_info_hdf5(filename)
    #print(mc_header)

    # vyber jednoho ze simulovanych telescopu
    param = param.where(param.tel_id == args.telescope)
    param = param.dropna()
    print(len(param))

    # vyhozeni eventu co se nenafitovaly po prisnejsich tailcutech
    param = param[param['intensity'] > 0]
    param = param.dropna()
    print(len(param))

    #mc_energy = (10**param.mc_energy)/1000.0  # TeV, POZOR - param.mc_energy je v log10(GeV)
    mc_energy = param.mc_energy     # TeV, v novych filech
    max_impact = mc_header.max_scatter_range
    energy_min = min(mc_energy)
    energy_max = max(mc_energy)
    spectral_index_sim = mc_header.spectral_index

    # Celkovy rate gamma z Craba s realnym spektrem (jakoby vsechno co prileti do simulovane oblasti)
    area_max = np.pi * max_impact**2
    const = 2.83*10**-7 # TeV^-1 m^-2 s^-1
    spectral_index_real = -2.62
    e0 = 1 # TeV
    # V tomhle je analyticky integrovany flux pres energie, nemusi se delat zadna oprava na logaritmicke biny, protoze tohle proste analytickej integral
    R_full = area_max * const * (energy_max**(spectral_index_real+1) - energy_min**(spectral_index_real+1)) / (spectral_index_real+1) / e0**spectral_index_real
    print('Rfull: ', R_full)

    # numericka integrace jako test
    I = quad(crab_hegra_dNdE, energy_min, energy_max)
    Rfull_num = area_max * I[0]
    print('Rfull (numericaly): ', Rfull_num)

    # Reweighting histogramu vsech thrown events na realne spektrum
    mid_energy = (hist.bins_energy[1:]+hist.bins_energy[:-1])/2.0
    all_energy_hist = np.sum(hist_merged.histogram, axis=1)
    all_energy_hist_weighted = all_energy_hist * mid_energy**(spectral_index_real - spectral_index_sim) * e0**(spectral_index_sim - spectral_index_real)
    factor =  sum(all_energy_hist) / sum(all_energy_hist_weighted)
    all_energy_hist_weighted = (factor * all_energy_hist_weighted).astype(int)
    print(sum(all_energy_hist), sum(all_energy_hist_weighted))

    # konstanta w_g(E) odvozena z notebooks/Calculate_sensitivity.ipynb
    # - kdyz tou konstantou prenasobim histogram triggerovanych eventu v energii a pak ho sectu, tak ziskam stejny trigger rate jako tim druhym pristupem
    # - podle Abelarda kdyz touhle konstantou prenasobim jakoukoli distribuci, tak dostanu rovnou Hz na ose y
    int_sim = (energy_max**(spectral_index_sim+1) - energy_min**(spectral_index_sim+1)) / (spectral_index_sim+1)
    w_g = mid_energy**(spectral_index_real - spectral_index_sim) / e0**spectral_index_real * area_max * const * int_sim / sum(all_energy_hist)
    #param_hist = np.histogram(param.mc_energy, bins=np.log10(hist.bins_energy*1e3))[0]
    param_hist = np.histogram(mc_energy, bins=hist.bins_energy)[0]
    print('Expected gamma rate from Crab using w_g [Hz] ', sum(param_hist * w_g))

    # Reweighting histogramu vsech trigerovanych thrown_event_distribution
    #param_hist = np.histogram(param.mc_energy, bins=np.log10(hist.bins_energy*1e3))[0]
    param_hist = np.histogram(mc_energy, bins=hist.bins_energy)[0]
    param_hist_weighted = param_hist * mid_energy**(spectral_index_real - spectral_index_sim) * e0**(spectral_index_sim - spectral_index_real)
    param_hist_weighted = (factor * param_hist_weighted).astype(int)

    # Probability of detection as a sum for all energy_bins_edges
    # - suma vsech detekovanych vs suma vsech simulovanych
    detection_probability = np.sum(param_hist_weighted) / np.sum(all_energy_hist_weighted )

    # Estimation of observed total proton rate
    R_obs_tot = detection_probability * R_full
    print('Expected gamma rate from Crab [Hz] ', R_obs_tot)

    # Ulozeni vahovaciho faktoru a histogramu thrown eventu pro kazdy energeticky bin
    if args.output:
        energy_bins = pd.Series(hist.bins_energy)
        weights = pd.Series(w_g)
        thrown_hist = pd.Series(all_energy_hist)
        energy_bins.to_hdf(args.output, mode='w', key='energy_bins')
        weights.to_hdf(args.output, mode='a', key='weights')
        thrown_hist.to_hdf(args.output, mode='a', key='thrown_hist')

    # plot histogramu energy pro vsechny thrown showers vs survived lstchain
    fig = plt.figure(figsize=(6,5))
    ax = fig.add_subplot(1, 1, 1)
    plt.step(mid_energy, np.sum(hist_merged.histogram, axis=1), where='mid', label='all thrown, sim spec')
    plt.step(mid_energy, all_energy_hist_weighted, where='mid', label='all thrown, weighted')
    plt.step(mid_energy, param_hist, where='mid', label='triggered, sim spec')
    plt.step(mid_energy, param_hist_weighted, where='mid', label='triggered, weighted')
    ax.set_yscale('log')
    ax.set_xscale('log')
    ax.set_xlabel('Energy [TeV]')
    ax.set_ylabel('N')
    plt.title('Gamma statistics')
    plt.grid()
    plt.legend()
    plt.savefig(args.output.rpartition('.h5')[0] + '_stats.png')
    plt.close(fig)

    # trigger rate as a function of energy
    plt.figure(figsize=(6,5))
    #plt.plot(mid_energy, dR_obs_dE, '-')
    plt.step(mid_energy, param_hist*w_g, where='mid', label='weighted')
    plt.ylabel('Event rate [Hz]')
    plt.xlabel('E [TeV]')
    plt.xscale('log')
    plt.yscale('log')
    plt.title('Gamma event rate')
    plt.grid()
    plt.tight_layout()
    plt.savefig(args.output.rpartition('.h5')[0] + '_rate.png')
    plt.close(fig)
