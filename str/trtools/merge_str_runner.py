#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
This script merges GangSTR or ExpansionHunter vcf.gz files into one combined VCF. 
Please ensure merge_prep.py has been run on the vcf files prior to running mergeSTR.py

For example: 
analysis-runner --access-level test --dataset tob-wgs --description 'tester --output-dir 'tester' mergeSTR.py --caller=eh --input-dir=gs://cpg-tob-wgs-main/str/expansionhunter/pure_repeats --dataset=tob-wgs TOBXXXX TOBXXXX

Required packages: sample-metadata, hail, click, os
pip install sample-metadata hail click
"""
import os

import click

from sample_metadata.apis import SampleApi

from cpg_utils.config import get_config
from cpg_utils.hail_batch import output_path
from cpg_workflows.batch import get_batch


config = get_config()

TRTOOLS_IMAGE = config['images']['trtools']


# inputs:
# caller
@click.option(
    '--caller',
    help='gangstr or eh',
    type=click.Choice(['eh', 'gangstr'], case_sensitive=True),
)
# dataset
@click.option('--dataset', help='dataset eg tob-wgs')
# input directory
@click.option('--input-dir', help='gs://...')
# input sample ID
@click.argument('external-wgs-ids', nargs=-1)
@click.command()
def main(
    caller, dataset, input_dir, external_wgs_ids: list[str]
):  # pylint: disable=missing-function-docstring

    # Initializing Batch
    b = get_batch()

    external_id_to_cpg_id: dict[str, str] = SampleApi().get_sample_id_map_by_external(
        dataset, list(external_wgs_ids)
    )

    vcf_input = []
    if caller == 'eh':
        for id in list(external_id_to_cpg_id.values()):
            sample_vcf_file = b.read_input_group(
                vcf=os.path.join(input_dir, f'{id}_eh.reheader.vcf.gz'),
                tbi=os.path.join(input_dir, f'{id}_eh.reheader.vcf.gz.tbi'),
            )
            vcf_input.append(sample_vcf_file.vcf)

    elif caller == 'gangstr':
        for id in list(external_id_to_cpg_id.values()):
            sample_vcf_file = b.read_input_group(
                vcf=os.path.join(input_dir, f'{id}_gangstr.vcf.gz'),
                tbi=os.path.join(input_dir, f'{id}_gangstr.vcf.gz.tbi'),
            )
            vcf_input.append(sample_vcf_file.vcf)
    else:
        raise Exception('Invalid caller')
    multi_vcf_file_path_string = ','.join(str(vcf_path) for vcf_path in vcf_input)

    trtools_job = b.new_job(name='mergeSTR')
    trtools_job.image(TRTOOLS_IMAGE)
    trtools_job.storage('20G')
    trtools_job.cpu(8)

    trtools_job.declare_resource_group(ofile={'vcf': '{root}.vcf'})

    trtools_job.command(
        f"""
     
    mergeSTR --vcfs {multi_vcf_file_path_string} --out {trtools_job.ofile} --vcftype {caller}
     
    """
    )
    num_samples = len(vcf_input)

    output_path_vcf = output_path(f'mergeSTR_{num_samples}_samples_{caller}')
    b.write_output(trtools_job.ofile, output_path_vcf)

    b.run(wait=False)


if __name__ == '__main__':
    main()  # pylint: disable=no-value-for-parameter