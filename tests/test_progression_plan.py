"""Native mission registration policy, independent of engine/network/platform."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ProgressionPlan(unittest.TestCase):
    def test_policy_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='alliance-progression-policy-') as temp:
            exe=Path(temp)/'test'
            cmd=[os.environ.get('CXX','g++'),'-std=c++17','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
                 '-I',str(ROOT),str(ROOT/'tests/progression_plan_fixture.cpp'),str(ROOT/'alliance_game/progression_plan.cpp'),'-o',str(exe)]
            subprocess.run(cmd,check=True,capture_output=True,text=True)
            result=subprocess.run([str(exe)],check=True,capture_output=True,text=True)
            self.assertIn('PASS progression plan checks=',result.stdout)
            print(result.stdout.strip(),flush=True)

if __name__=='__main__':unittest.main()
