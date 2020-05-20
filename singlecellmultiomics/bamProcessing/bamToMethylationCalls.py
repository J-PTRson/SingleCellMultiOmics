#!/usr/bin/env python
# -*- coding: utf-8 -*-

import matplotlib
matplotlib.rcParams['figure.dpi'] = 160
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import multiprocessing
from singlecellmultiomics.bamProcessing.bamBinCounts import generate_commands, count_methylation_binned
import argparse
from colorama import Fore, Style
from singlecellmultiomics.utils import dataframe_to_wig
from singlecellmultiomics.methylation import MethylationCountMatrix
from singlecellmultiomics.bamProcessing.bamFunctions import get_reference_from_pysam_alignmentFile
from colorama import Fore,Style


def prefilter(counts, cell_names, min_measurements, min_variance):
    if min_measurements>0:
        counts = counts.loc[:, (counts >= 0).sum() > min_measurements]
    if min_variance>0:
        return counts.loc[:, counts.var() >= min_variance].reindex(cell_names)
    else:
        return counts.reindex(cell_names)


def panda_and_prefilter(args):
    d, args = args # counts_dict ,(cell_names, min_measurements, min_variance)
    return prefilter(pd.DataFrame(d), *args)


def get_methylation_count_matrix(bam_path,
                                 bin_size: int,
                                 bp_per_job: int,
                                 min_samples: int = None,
                                 min_variance: int = None,
                                 min_mapping_qual: int = None,
                                 skip_contigs: set = None,
                                 known_variants: str = None,
                                 maxtime: int = None,
                                 head: int=None,
                                 threads: int = None,
                                **kwargs
                                 ):


    all_kwargs = {'known': known_variants,
            'maxtime': maxtime,
            'single_location':bin_size==1,
            'min_samples':min_samples,
            'min_variance':min_variance,
            'threads':threads
            }
    all_kwargs.update(kwargs)
    commands = generate_commands(
        alignments_path=bam_path,
        bin_size=bin_size if bin_size!=1 else bp_per_job,
        key_tags=None,
        max_fragment_size=0,
        dedup=True,
        head=head,
        bins_per_job= int(bp_per_job / bin_size) if bin_size!=1 else 1, min_mq=min_mapping_qual,
        kwargs=all_kwargs,
        skip_contigs=skip_contigs
    )


    count_mat = MethylationCountMatrix()
    if threads==1:
        for command in commands:
            result = count_methylation_binned(command)
            #result.prune(min_samples=min_samples, min_variance=min_variance)
            count_mat.update( result )
    else:
        with multiprocessing.Pool(threads) as workers:

            for result in workers.imap_unordered(count_methylation_binned, commands):
                #result.prune(min_samples=min_samples, min_variance=min_variance)
                count_mat.update(result)

    return count_mat



if __name__ == '__main__':
    argparser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="""Extract methylation calls from bam file
    """)
    argparser.add_argument('bamfile', metavar='bamfile', type=str)
    argparser.add_argument('-bin_size', default=500, type=int, help='bin size, set to 1 for single CpG')
    argparser.add_argument('-bp_per_job', default=1_000_000, type=int, help='Amount of basepairs to be processed per thread per chunk')
    argparser.add_argument('-threads', default=None, type=int, help='Amount of threads to use for counting, None to use the amount of available threads')
    argparser.add_argument('-threads_agg', default=1, type=int, help='Amount of threads to use for aggregation. Aggregation is very memory intensive, so this amount of threads should probably be lower than -threads')

    fi = argparser.add_argument_group("Filters")
    fi.add_argument('-min_variance', default=None, type=float)
    fi.add_argument('-min_mapping_qual', default=40, type=int)
    fi.add_argument('-head', default=None, type=int,help='Process the first n bins')
    fi.add_argument('-min_samples', default=1, type=int)
    fi.add_argument('-skip_contigs', type=str, help='Comma separated contigs to skip', default='MT,chrM')
    fi.add_argument('-known_variants',
                           help='VCF file with known variants, will be not taken into account as methylated/unmethylated',
                           type=str)

    og = argparser.add_argument_group("Output")
    #og.add_argument('-bed', type=str, help='Bed file to write methylation calls to')
    og.add_argument('-wig_beta', type=str, help='WIG file to write mean methylation per bin to')
    og.add_argument('-wig_n_samples', type=str, help='WIG file to write amount of samples covering to')

    og.add_argument('-betas', type=str, help='CSV or pickle file to write single cell methylation betas to')
    #og.add_argument('-mets', type=str, help='CSV or pickle file to write single cell methylation unmethylated frame to')
    #og.add_argument('-unmets', type=str, help='CSV or pickle file to write single cell methylation frame to')

    og.add_argument('-bismark_tabfile', type=str, help='Tabulated file to write to, contains: chr | start | end | unmethylated_counts | methylated_counts | beta_value')
    og.add_argument('-tabfile', type=str,
                    help='Tabulated file to write to, contains: chr | start | end | unmethylated_counts | methylated_counts | beta_value | variance | n_samples')
    og.add_argument('-distmat', type=str, help='CSV or pickle file to write single cell distance matrix to')
    og.add_argument('-distmat_plot', type=str, help='.PNG file to write distance matrix image to')
    args = argparser.parse_args()

    if args.distmat_plot is not None and not args.distmat_plot.endswith('.png'):
        args.distmat_plot += '.png'

    print('Obtaining counts ', end="")
    counts = get_methylation_count_matrix(bam_path = args.bamfile,
                                 bin_size = args.bin_size,
                                 bp_per_job = args.bp_per_job,
                                 min_samples = args.min_samples,
                                 min_variance = args.min_variance,
                                 known_variants = args.known_variants,
                                 skip_contigs = args.skip_contigs.split(','),
                                 min_mapping_qual=args.min_mapping_qual,
                                 head = args.head,
                                 threads=args.threads,
                                 single_location= (args.bin_size==1)
    )
    print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")

    print(counts)

    if args.betas is not None:
        print('Writing counts ', end="")
        if args.betas.endswith('.pickle.gz'):
            counts.to_pickle(args.betas)
        else:
            counts.get_frame('beta').to_csv(args.betas)
        print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")

    if args.wig_beta is not None:
        # Calculate mean beta value and write to wig:
        print('Writing WIG beta', end="")
        dataframe_to_wig(counts.get_bulk_frame()[['beta']], args.wig_beta, span=args.bin_size)
        print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")

    if args.wig_n_samples is not None:
        # Calculate mean beta value and write to wig:
        print('Writing WIG n_samples', end="")
        dataframe_to_wig(counts.get_bulk_frame()[['n_samples']], args.wig_n_samples, span=args.bin_size)
        print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")

    if args.distmat is not None or args.distmat_plot is not None:
        print('Calculating distance matrix', end="")
        dmat = counts.get_sample_distance_matrix()
        print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")
        if args.distmat_plot is not None:
            print('Writing distmat_image', end="")
            try:
                sns.clustermap(dmat)
                plt.tight_layout()
                plt.savefig(args.distmat_plot)
                print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")
            except Exception as e:
                print(e)
                print(f" [ {Fore.RED}FAIL{Style.RESET_ALL} ] ")

        if args.distmat is not None:
            print('Writing distance matrix', end="")
            if args.distmat.endswith('.pickle.gz'):
                dmat.to_pickle(args.distmat)
            else:
                dmat.to_csv(args.distmat, sep='\t')
            print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")

    if args.bismark_tabfile is not None:
        print('Writing bismark_tabfile matrix', end="")
        bf = counts.get_bulk_frame()
        bf.index.set_names(['chr','start', 'end'], inplace=True)
        bf[['unmethylated', 'methylated', 'beta']].to_csv(args.bismark_tabfile, sep='\t')
        print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")

    if args.tabfile is not None:
        print('Writing tabfile', end="")
        if args.tabfile.endswith('.pickle.gz'):
            counts.get_bulk_frame().to_pickle(args.tabfile)
        else:
            counts.get_bulk_frame().to_csv(args.tabfile, sep='\t')
        print(f" [ {Fore.GREEN}OK{Style.RESET_ALL} ] ")