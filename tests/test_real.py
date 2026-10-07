import os, subprocess, sys, unittest
class RealMode(unittest.TestCase):
    def test_real_mode(self):
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_mode_check.py")], capture_output=True, text=True)
        self.assertIn("REAL MODE OK", r.stdout, r.stdout + r.stderr)
if __name__ == "__main__": unittest.main()
