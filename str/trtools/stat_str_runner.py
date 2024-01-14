#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
This script runs statSTR() from TRTools package on a single/merged STR vcf file and outputs various statistics

For example:
analysis-runner --access-level test --dataset tob-wgs --description 'tester' --output-dir 'tester' statSTR_runner.py --caller=eh --file-path=gs://cpg-tob-wgs-test/hoptan-str/mergeSTR/mergeSTR_2_samples_gangstr.vcf

Required packages: sample-metadata, hail, click, os
pip install sample-metadata hail click
"""

import click

from cpg_utils.config import get_config
from cpg_utils.hail_batch import get_batch, output_path


config = get_config()

TRTOOLS_IMAGE = config['images']['trtools']


# inputs:
# file-path
@click.option('--file-path', help='gs://...')
# caller
@click.option(
    '--caller',
    help='gangstr or eh',
    type=click.Choice(['eh', 'gangstr'], case_sensitive=True),
)
@click.option(
    '--job-storage', help='Storage of the Hail batch job eg 30G', default='50G'
)
@click.option('--job-memory', help='Memory of the Hail batch job eg 64G', default='32G')
@click.command()
def main(
    file_path, caller, job_storage, job_memory
):  # pylint: disable=missing-function-docstring
    # Initializing Batch
    b = get_batch()
    vcf_input = b.read_input(file_path)
    trtools_job = b.new_job(name=f'statSTR {caller}')

    trtools_job.image(TRTOOLS_IMAGE)
    trtools_job.storage(job_storage)
    trtools_job.memory(job_memory)

    trtools_job.declare_resource_group(ofile={'tab': '{root}.tab'})

    trtools_job.command(
        f"""
        set -ex;
        statSTR --vcf {vcf_input} --vcftype {caller} --out {trtools_job.ofile} --thresh --afreq --acount --hwep --het --entropy --mean --mode --var --numcalled

        """
    )

    output_path_vcf = output_path(f'statSTR_samples_{caller}', 'analysis')
    b.write_output(trtools_job.ofile, output_path_vcf)

    b.run(wait=False)


if __name__ == '__main__':
    main()  # pylint: disable=no-value-for-parameter
