import argparse
import socket
import sys
import signal
from toolset.benchmark.benchmarker import Benchmarker
from toolset.utils.scaffolding import Scaffolding
from toolset.utils import cleaner
from toolset.utils.results_helper import Results
from toolset.utils.benchmark_config import BenchmarkConfig
from toolset.utils import docker_helper
from toolset.utils.metadata_helper import gather_tests
from toolset.utils.output_helper import log

# Enable cross-platform colored output
from colorama import init, Fore
init()

# Required to be globally known
config = None


class StoreSeqAction(argparse.Action):
    '''
    Helper class for parsing a sequence from the command line
    '''

    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        super(StoreSeqAction, self).__init__(
            option_strings, dest, type=str, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, self.parse_seq(values))

    def parse_seq(self, argument):
        result = argument.split(',')
        sequences = [x for x in result if ":" in x]
        for sequence in sequences:
            try:
                (start, step, end) = sequence.split(':')
            except ValueError:
                log("  Invalid: {!s}".format(sequence), color=Fore.RED)
                log("  Requires start:step:end, e.g. 1:2:10", color=Fore.RED)
                raise
            result.remove(sequence)
            result = result + range(int(start), int(end), int(step))
        return [abs(int(item)) for item in result]


def __stop(signal, frame):
    log("Shutting down (may take a moment)")
    docker_helper.stop(config)
    sys.exit(0)


signal.signal(signal.SIGTERM, __stop)
signal.signal(signal.SIGINT, __stop)


###################################################################################################
# Main
###################################################################################################
def main(argv=None):
    '''
    Runs the toolset.
    '''
    # Do argv default this way, as doing it in the functional declaration sets it at compile time
    if argv is None:
        argv = sys.argv

    ##########################################################
    # Set up argument parser
    ##########################################################
    parser = argparse.ArgumentParser(
        description="Install or run the Framework Benchmarks test suite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=
        '''If an argument includes (type int-sequence), then it accepts integer lists in multiple forms.
        Using a single number e.g. 5 will create a list [5]. Using commas will create a list containing those
        values e.g. 1,3,6 creates [1, 3, 6]. Using three colon-separated numbers of start:step:end will create a
        list, using the semantics of python's range function, e.g. 1:3:15 creates [1, 4, 7, 10, 13] while
        0:1:5 creates [0, 1, 2, 3, 4]
        ''')

    # Suite options
    parser.add_argument(
        '--build',
        nargs='+',
        help='Builds the dockerfile(s) for the given test(s)')
    parser.add_argument(
        '--clean',
        action='store_true',
        default=False,
        help='Removes the results directory')
    parser.add_argument(
        '--new',
        action='store_true',
        default=False,
        help='Initialize a new framework test')
    parser.add_argument(
        '--quiet',
        action='store_true',
        default=False,
        help=
        'Only print a limited set of messages to stdout, keep the bulk of messages in log files only'
    )
    parser.add_argument(
        '--results-name',
        help='Gives a name to this set of results, formatted as a date',
        default='(unspecified, datetime = %Y-%m-%d %H:%M:%S)')
    parser.add_argument(
        '--results-environment',
        help='Describes the environment in which these results were gathered',
        default='(unspecified, hostname = %s)' % socket.gethostname())
    parser.add_argument(
        '--results-upload-uri',
        default=None,
        help=
        'A URI where the in-progress results.json file will be POSTed periodically'
    )
    parser.add_argument(
        '--parse',
        help=
        'Parses the results of the given timestamp and merges that with the latest results'
    )

    # Test options
    parser.add_argument(
        '--test', default=None, nargs='+', help='names of tests to run')
    parser.add_argument(
        '--test-dir',
        nargs='+',
        dest='test_dir',
        help='name of framework directory containing all tests to run')
    parser.add_argument(
        '--test-lang',
        nargs='+',
        dest='test_lang',
        help='name of language directory containing all tests to run')
    parser.add_argument(
        '--exclude', default=None, nargs='+', help='names of tests to exclude')
    parser.add_argument(
        '--type',
        choices=[
            'all', 'json', 'db', 'query', 'cached_query', 'fortune', 'update',
            'plaintext'
        ],
        default='all',
        help='which type of test to run')
    parser.add_argument(
        '-m',
        '--mode',
        choices=['benchmark', 'verify', 'debug'],
        default='benchmark',
        help=
        'verify mode will only start up the tests, curl the urls and shutdown. debug mode will skip verification and leave the server running.'
    )
    parser.add_argument(
        '--list-tests',
        action='store_true',
        default=False,
        help='lists all the known tests that can run')

    # Benchmark options
    parser.add_argument(
        '--duration',
        default=15,
        help='Time in seconds that each test should run for.')
    parser.add_argument(
        '--server-host',
        default='tfb-server',
        help='Hostname/IP for application server')
    parser.add_argument(
        '--database-host',
        default='tfb-database',
        help='Hostname/IP for database server')
    parser.add_argument(
        '--client-host', default='', help='Hostname/IP for client server')
    parser.add_argument(
        '--concurrency-levels',
        nargs='+',
        default=[16, 32, 64, 128, 256, 512],
        help='List of concurrencies to benchmark')
    parser.add_argument(
        '--pipeline-concurrency-levels',
        nargs='+',
        default=[256, 1024, 4096, 16384],
        help='List of pipeline concurrencies to benchmark')
    parser.add_argument(
        '--query-levels',
        nargs='+',
        default=[1, 5, 10, 15, 20],
        help='List of query levels to benchmark')
    parser.add_argument(
        '--cached-query-levels',
        nargs='+',
        default=[1, 10, 20, 50, 100],
        help='List of cached query levels to benchmark')

    # Network options
    parser.add_argument(
        '--network-mode',
        default=None,
        help='The network mode to run docker in')

    args = parser.parse_args()

    global config
    config = BenchmarkConfig(args)
    results = Results(config)

    if config.new:
        Scaffolding(config)

    elif config.build:
        docker_helper.build(config, config.build)

    elif config.clean:
        cleaner.clean(results)
        docker_helper.clean(config)

    elif config.list_tests:
        all_tests = gather_tests(benchmarker_config=config)

        for test in all_tests:
            log(test.name)

    elif config.parse != None:
        # TODO: broken
        all_tests = gather_tests(benchmarker_config=config)

        for test in all_tests:
            test.parse_all()

        results.parse(all_tests)

    else:
        benchmarker = Benchmarker(config, results)
        any_failed = benchmarker.run()
        if config.mode == "verify":
            return any_failed

    return 0


if __name__ == "__main__":
    sys.exit(main())
