#!/usr/bin/env python3
"""
This script calculates cell-type specific PCs from the pseudobulk RNA data (genome-wide),
merges them with other pre-calculated covariates, and writes the file to GCP.

analysis-runner --access-level test --dataset bioheart --image australia-southeast1-docker.pkg.dev/cpg-common/images/scanpy:1.9.3 \
--description "Get covariates" --output-dir "str/associatr/rna_pc_calibration/2_pcs/input_files" get_covariates.py --input-dir=gs://cpg-bioheart-test/str/associatr/input_files/pseudobulk/pseudobulk \
--cell-types=CD8_TEM --chromosomes=1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22 --covariate-file-path=gs://cpg-bioheart-test/str/anndata/saige-qtl/input_files/covariates/sex_age_geno_pcs_tob_bioheart.csv \
--num-pcs=2

"""

import logging

import click
import pandas as pd
import scanpy as sc

import hail as hl

from cpg_utils import to_path
from cpg_utils.hail_batch import get_batch, init_batch, output_path


def get_covariates(pseudobulk_input_dir, cell_type, chromosomes, covariate_file_path, num_pcs):
    """
    Calculates cell-type specific PCs from the pseudobulk data (genome-wide),
    merges them with other pre-calculated covariates, and writes file to GCP
    """
    init_batch()

    # read in and concatenate pseudobulk data (chr-specific --> genome-wide anndata)
    adatas = []
    for i in chromosomes.split(','):
        gcs_file_path = f'{pseudobulk_input_dir}/{cell_type}/{cell_type}_chr{i}_pseudobulk.csv'
        print(f'Loading {gcs_file_path}...')

        local_file_path = to_path(gcs_file_path).copy('here.csv')
        adata = sc.read_csv(local_file_path)
        adatas.append(adata)
    adata_genome = sc.concat(adatas, axis=1)

    # subset to high variance genes prior to PCA
    sc.pp.highly_variable_genes(adata_genome, min_mean=0.0125, max_mean=3, min_disp=0.5)
    adata_genome = adata_genome[:, adata_genome.var.highly_variable]

    # unit variance scaling for PCA
    sc.pp.scale(adata_genome, max_value=10)

    # PCA
    sc.tl.pca(adata_genome, svd_solver='arpack')

    # Variance ratio plot
    sc.pl.pca_variance_ratio(adata_genome, save='local.png')
    hl.hadoop_copy(
        'figures/pca_variance_ratiolocal.png',
        output_path(f'pseudobulk_RNA_PCs_scree_plots/{cell_type}_scree_plot.png'),
    )

    # extract PCs
    df_pcs = pd.DataFrame(adata_genome.obsm['X_pca'])
    df_pcs.index = adata_genome.obs.index
    df_pcs = df_pcs.rename_axis('sample_id').reset_index()  # index (CPG ids) are not stored in 'sample_id' column
    df_pcs = df_pcs[['sample_id'] + list(range(num_pcs))]
    df_pcs = df_pcs.rename(columns={i: f'rna_PC{i+1}' for i in range(num_pcs)})  # rename PC columns: rna_PC{num}

    # read in covariates
    cov = pd.read_csv(covariate_file_path)

    # Get the index of the column 'geno_PC7'
    index_of_excluded_geno_pc = cov.columns.get_loc('geno_PC7')
    # Drop columns from 'geno_PC7' onwards (use the first 6 geno PCs only)
    cov = cov.iloc[:, :index_of_excluded_geno_pc]

    # ruff error:
    # PD015 Use `.merge` method instead of `pd.merge` function.
    # They have equivalent functionality.
    merged_df = cov.merge(df_pcs, on='sample_id')

    # write to GCP
    merged_df.to_csv(
        output_path(f'covariates/{num_pcs}_rna_pcs/{cell_type}_covariates.csv'),
        index=False,
    )


# inputs:
@click.option('--input-dir', help='GCS Path to the input dir storing pseudobulk CSV files')
@click.option('--cell-types', help='Name of the cell type, comma separated if multiple')
@click.option('--chromosomes', help='Chromosome number eg 1, comma separated if multiple')
@click.option('--job-storage', help='Storage of the batch job eg 30G', default='8G')
@click.option('--job-memory', help='Memory of the batch job', default='standard')
@click.option('--job-cpu', help='Number of CPUs of Hail batch job', default=2)
@click.option('--covariate-file-path', help='GCS Path to the existing covariate file')
@click.option('--num-pcs', help='Number of PCs to calculate', default=20)
@click.command()
def main(
    input_dir,
    cell_types,
    chromosomes,
    job_storage,
    job_memory,
    job_cpu,
    covariate_file_path,
    num_pcs,
):
    """
    Obtain cell-type specific covariates for pseudobulk associaTR model

    """
    b = get_batch()

    logging.info(f'Cell types to run: {cell_types}')
    logging.info(f'Chromosomes to run: {chromosomes}')

    for cell_type in cell_types.split(','):
        j = b.new_python_job(name=f'Obtain covariates for for {cell_type}, using chr:{chromosomes}')
        j.image('australia-southeast1-docker.pkg.dev/cpg-common/images/scanpy:1.9.3')
        j.cpu(job_cpu)
        j.memory(job_memory)
        j.storage(job_storage)
        j.call(
            get_covariates,
            input_dir,
            cell_type,
            chromosomes,
            covariate_file_path,
            num_pcs,
        )

    b.run(wait=False)


if __name__ == '__main__':
    main()  # pylint: disable=no-value-for-parameter
