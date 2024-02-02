#!/usr/bin/env python3

"""
This script combines sharded output VCFs from mergeSTR into one VCF, and assumes the genotyper used was ExpansionHunter.

analysis-runner --access-level standard --dataset tob-wgs --description  \
    'VCF combiner' --memory highmem --cpu 16 --output-dir 'str/5M_run_combined_vcfs/vcf_combiner_output/v5' \
    merge_str_vcf_combiner.py \
    --input-dir=gs://cpg-tob-wgs-main-analysis/str/5M_run_combined_vcfs/merge_str/v5
"""
import gzip
import click


from cpg_utils import to_path
from cpg_utils.hail_batch import output_path


@click.command()
@click.option('--input-dir', help='Parent input directory for sharded VCFs')
@click.option('--output', help='Name of output VCF', default='combined_eh.vcf')
def main(input_dir, output):
    """
    Combines sharded mergeSTR output VCFs in input_dir into one combined VCF,
    writing it to a GCS output path
    Takes an input directory containing vcf shards
    Aggregates all sharded data into a single output file

    Doesn't use batch any more, as mem, cpu and disk can all be analysis-runner CLI args
    """

    # make input_files GSPath elements into a string type object
    input_file_paths = map(str, to_path(input_dir).glob('*.vcf.gz'))

    # create a dictionary where key is the shard number and value is the path to the shard
    input_files_dict = {
        int(file_path.split('eh_shard')[1].split('.')[0]): file_path
        for file_path in input_file_paths
    }

    with to_path(output_path(output, 'analysis')).open('w') as handle:
        # Process each input file
        for key in sorted(input_files_dict.keys()):
            input_file = to_path(input_files_dict[key])
            print(f'Parsing {input_file}')

            with gzip.open(input_file, 'rt') as f:
                for line in f:
                    # Collect information from the header lines
                    if line.startswith('##fileformat'):
                        if key == 1:  # first file processed is shard_1
                            handle.write(line)
                    elif (
                        line.startswith('##INFO')
                        or line.startswith('##FILTER')
                        or line.startswith('##FORMAT')
                        or line.startswith('##contig')
                        or line.startswith('##command')
                    ):
                        if key == 1:
                            handle.write(line)
                    elif line.startswith('#CHROM'):
                        if key == 1:
                            handle.write(line)
                    elif not line.startswith('#'):
                        handle.write(line)

    print(f'Parsed {len(list(input_files_dict.keys()))} sharded VCFs')


if __name__ == '__main__':
    main()  # pylint: disable=no-value-for-parameter
