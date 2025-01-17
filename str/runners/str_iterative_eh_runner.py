#!/usr/bin/env python3
# pylint: disable=import-error, too-many-locals

"""
This script uses ExpansionHunterv5 to call STRs on WGS cram files.
Required input: --variant-catalog (file path to variant catalog, can be sharded or unsharded), --dataset, and sample mapping file [CSV] (CPG sample id (first column) and sex (third column))
EH will run on every sample listed inthe sample mapping file.

For example:
analysis-runner --access-level test --dataset tob-wgs --description 'n100 5M run' --output-dir 'str/5M_run' str_iterative_eh_runner.py --variant-catalog=gs://cpg-tob-wgs-test/hoptan-str/5M_run/5M_sharded_100k/ --dataset=tob-wgs-test --sample-id-file=gs://cpg-tob-wgs-test/hoptan-str/5M_run/n1_test_file.csv

Required packages: str_iterative_eh_runner_requirements.txt

"""
import re

import click

from cpg_utils import to_path
from cpg_utils.config import get_config
from cpg_utils.hail_batch import get_batch, image_path, output_path, reference_path
from metamist.graphql import gql, query

config = get_config()


def extract_number(file_name: str) -> int:
    """Extracts the number from a file name. Used to sort a list of files by the chunk number"""
    file_name = file_name.split('/')[-1]

    # this could do with a second pair of eyeballs
    if result := re.search(r'\d+', file_name):
        return int(result.group())

    raise ValueError('File name does not contain a number: ', file_name)


# inputs:
@click.option(
    '--variant-catalog',
    help='Full path to Illumina Variants catalog (sharded/unsharded)',
)
@click.option('--dataset', help='dataset eg tob-wgs')
@click.option(
    '--max-parallel-jobs',
    type=int,
    default=50,
    help='To avoid exceeding Google Cloud quotas, set this concurrency as a limit.',
)
# sample id and sex mapping file
@click.option('--sample-id-file', help='Full path to mapping of CPG id and sex')
@click.option('--job-storage', help='Storage of the Hail batch job eg 30G', default='50G')
@click.option('--job-memory', help='Memory of the Hail batch job', default='32G')
@click.option('--job-ncpu', help='Number of CPUs of the Hail batch job', default=8)
@click.option(
    '--output-bam-json',
    is_flag=True,
    help='Outputs realigned bam and JSON files (False = VCF only)',
)
@click.option('--output-file-name', help='Name of output file', default=None)
@click.command()
def main(
    variant_catalog: str,
    dataset: str,
    max_parallel_jobs: int,
    sample_id_file: str,
    job_storage: str,
    job_memory: str,
    job_ncpu: int,
    output_bam_json: bool,
    output_file_name: str,
):
    """
    Main function to run ExpansionHunter on WGS cram files.
    Args:
        variant_catalog (str):
        dataset (str):
        max_parallel_jobs (int):
        sample_id_file (str):
        job_storage (str):
        job_memory (str):
        job_ncpu (int):
        output_bam_json (bool):
        output_file_name (str):
    """
    b = get_batch()

    # Reference fasta
    if 'hgdp' in dataset or 'thousand-genomes' in dataset:
        ref_fasta = 'gs://cpg-common-main/references/hg38/v0/Homo_sapiens_assembly38.fasta'
    else:
        ref_fasta = str(reference_path('broad/ref_fasta'))
    ref = b.read_input_group(
        **dict(
            base=ref_fasta,
            fai=ref_fasta + '.fai',
            dict=ref_fasta.replace('.fasta', '').replace('.fna', '').replace('.fa', '') + '.dict',
        ),
    )

    # list of catalog files (multiple, if catalog is sharded)
    catalog_files = list(to_path(variant_catalog).glob('*.json'))
    catalog_files = [str(gs_path) for gs_path in catalog_files]  # coverts into a string type

    # sort catalog files list by chunk number
    catalog_files = sorted(catalog_files, key=extract_number)

    # track number of jobs running
    jobs: list = []

    # open sample-sex mapping file
    with to_path(sample_id_file).open() as f:
        # Iterate over each sample to call Expansion Hunter
        for line in f:
            split_line = line.split(',')
            cpg_id = split_line[0]
            sex = split_line[2]
            sex = sex.replace('\n', '')
            if cpg_id == 's':  # header line
                continue
            if sex == 'XY':
                sex_param = 'male'
            else:
                sex_param = 'female'
                # 'X' and 'ambiguous' karyotypic sex will be marked
                # as female (ExpansionHunter defaults to female if
                # no sex_parameter is provided)

            # retrieve corresponding cram path
            cram_retrieval_query = gql(
                """
                query MyQuery($dataset: String!,$cpg_id: [String!]!) {
            project(name: $dataset) {
                sequencingGroups(id: {in_: $cpg_id}) {
                id
                sample {
                    externalId
                }
                analyses(type: {eq: "cram"}, active: {eq: true}) {
                    output
                    timestampCompleted
                }
                }
            }

            }
                """,
            )
            response = query(
                cram_retrieval_query,
                variables={'dataset': dataset, 'cpg_id': cpg_id},
            )

            # Making sure Hail Batch would localize both CRAM and the correponding CRAI index
            crams = b.read_input_group(
                **{
                    'cram': response['project']['sequencingGroups'][0]['analyses'][0]['output'],
                    'cram.crai': response['project']['sequencingGroups'][0]['analyses'][0]['output'] + '.crai',
                },
            )
            # per sample, run parallel jobs on each shard of the catalog
            for index, subcatalog in enumerate(catalog_files, start=1):
                if (
                    to_path(output_path(f'{cpg_id}/{cpg_id}_eh_shard{index}.vcf', 'analysis')).exists()
                    and not output_bam_json
                ):
                    continue
                # ExpansionHunter job initialisation
                eh_job = b.new_job(name=f'ExpansionHunter:{cpg_id} running  shard {index}/{len(catalog_files)}')
                eh_job.image(image_path('expansionhunter_bw2'))
                # limit parallelisation
                if len(jobs) >= max_parallel_jobs:
                    eh_job.depends_on(jobs[-max_parallel_jobs])
                jobs.append(eh_job)
                eh_job.storage(job_storage)
                eh_job.memory(job_memory)
                eh_job.cpu(job_ncpu)
                eh_regions = b.read_input(subcatalog)
                if output_bam_json:
                    eh_job.declare_resource_group(
                        eh_output={
                            'vcf': '{root}.vcf',
                            'json': '{root}.json',
                            'realigned.bam': '{root}_realigned.bam',
                        },
                    )
                else:
                    eh_job.declare_resource_group(
                        eh_output={
                            'vcf': '{root}.vcf',
                        },
                    )

                eh_job.command(
                    f"""
                ExpansionHunter  \\
                --reads {crams['cram']} \\
                --reference {ref.base} --variant-catalog {eh_regions}\\
                --threads 16 --analysis-mode streaming \\
                --output-prefix {eh_job.eh_output} \\
                --sex {sex_param}
                """,
                )
                if output_file_name is None:
                    # ExpansionHunter output writing
                    eh_output_path = output_path(f'{cpg_id}/{cpg_id}_eh_shard{index}', 'analysis')
                else:
                    eh_output_path = output_path(output_file_name, 'analysis')
                b.write_output(eh_job.eh_output, eh_output_path)

    b.run(wait=False)


if __name__ == '__main__':
    main()  # pylint: disable=no-value-for-parameter
