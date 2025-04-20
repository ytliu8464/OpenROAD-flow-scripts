#!/usr/bin/env tcsh

set index=test

set parallel_exec = (/home/tool/gnu/parallel/parallel-20160822/bin/parallel)
$parallel_exec --joblog p_$index.log \
--bar \
--sshloginfile node \
--workdir $PWD < joblist_$index
