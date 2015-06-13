from   __future__ import absolute_import, unicode_literals
from   nose.tools import ok_
from   streamparse.bin.run import subparser_hook
import argparse
import unittest



class RunTestCase(unittest.TestCase):
    def test_subparser_hook(self):
        parser     = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        subparser_hook(subparsers)

        subcommands = parser._optionals._actions[1].choices.keys()
        ok_('run' in subcommands)

if __name__ == '__main__':
    unittest.main()