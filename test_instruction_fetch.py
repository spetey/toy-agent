"""Regression tests for instruction fetch without Hamming decoding.

Run: python3 -m unittest -v test_instruction_fetch
"""

import itertools
from pathlib import Path
import unittest

from fb2d import (
    FB2DSimulator, OPCODES, DIR_E,
    cell_to_payload_raw, encode_opcode, hamming_encode,
)


PROGRAM = Path(__file__).parent / 'programs' / 'agent-v1-narrow-w46.fb2d'
PARITY_POSITIONS = (0, 1, 2, 4, 8)


def machine(word):
    sim = FB2DSimulator(rows=2, cols=4)
    sim.grid = [word, hamming_encode(5), hamming_encode(2),
                hamming_encode(1), hamming_encode(3), hamming_encode(4), 0, 0]
    sim.h0, sim.h1, sim.cl, sim.ex, sim.ix = 1, 2, 3, 4, 5
    return sim


def snapshot(sim):
    return (sim.grid[:], sim._capture_ip_state(), sim.step_count)


def agent_snapshot(sim):
    sim._save_active()
    return (sim.grid[:], [dict(ip) for ip in sim.ips], sim.step_count)


class InstructionFetchTests(unittest.TestCase):
    def test_every_payload_is_insensitive_to_all_parity_patterns(self):
        # Exercise all 65,536 instruction words. Vary parity while holding
        # the payload and all operand cells fixed: the selected operation,
        # pointer movement, and writes must be identical.
        for payload in range(2048):
            reference = machine(hamming_encode(payload))
            reference.step()
            expected = (reference.grid[1:], reference._capture_ip_state())
            for parity in range(32):
                word = hamming_encode(payload)
                for i, bit in enumerate(PARITY_POSITIONS):
                    if parity & (1 << i):
                        word ^= 1 << bit
                sim = machine(word)
                before = snapshot(sim)
                sim.step()
                self.assertEqual(sim.grid[0], word)
                self.assertEqual(
                    (sim.grid[1:], sim._capture_ip_state()), expected,
                    'payload={}, parity={}'.format(payload, parity))
                sim.step_back()
                self.assertEqual(snapshot(sim), before)

    def test_three_bit_miscorrection_no_longer_suppresses_plus(self):
        word = encode_opcode(OPCODES['+']) ^ (1 << 0) ^ (1 << 1) ^ (1 << 6)
        sim = machine(word)
        before = snapshot(sim)
        self.assertEqual(cell_to_payload_raw(word), 971)
        self.assertEqual(sim._cell_char(word), '+')
        sim.step()
        self.assertEqual(sim.grid[sim.h0], hamming_encode(6))
        sim.step_back()
        self.assertEqual(snapshot(sim), before)

    def test_two_payload_errors_are_nop_despite_parity(self):
        # Hamming decoding previously rescued this five-bit corruption.
        word = encode_opcode(OPCODES['+'])
        for bit in (0, 1, 2, 3, 5):
            word ^= 1 << bit
        sim = machine(word)
        before = snapshot(sim)
        self.assertEqual(cell_to_payload_raw(word), 972)
        self.assertEqual(sim._cell_char(word), '\u00b7')
        sim.step()
        self.assertEqual(sim.grid, before[0])
        self.assertEqual((sim.ip_row, sim.ip_col, sim.ip_dir), (0, 1, DIR_E))
        sim.step_back()
        self.assertEqual(snapshot(sim), before)

    def test_rotation_operand_still_uses_hamming_decoding(self):
        sim = machine(encode_opcode(OPCODES['R']))
        sim.grid[sim.h0] = 2
        sim.grid[sim.cl] = hamming_encode(1) ^ (1 << 3)
        before = snapshot(sim)
        sim.step()
        self.assertEqual(sim.grid[sim.h0], 1)
        self.assertEqual(sim.grid[sim.cl], before[0][sim.cl])
        sim.step_back()
        self.assertEqual(snapshot(sim), before)


class WikivoreTests(unittest.TestCase):
    def test_clean_agent_round_trip(self):
        sim = FB2DSimulator()
        sim.load_state(str(PROGRAM))
        before = agent_snapshot(sim)
        for _ in range(10000):
            sim.step_all()
        self.assertNotEqual(agent_snapshot(sim), before)
        for _ in range(10000):
            sim.step_back_all()
        self.assertEqual(agent_snapshot(sim), before)

    def test_agent_repairs_single_and_double_bit_errors(self):
        # The first cell scanned by IP0 is an existing NOP filler in the
        # partner's code. Test every single-bit error and every pair of
        # distinct errors, including parity errors, with the real program.
        errors = [(bit,) for bit in range(16)]
        errors += list(itertools.combinations(range(16), 2))
        for bits in errors:
            with self.subTest(bits=bits):
                sim = FB2DSimulator()
                sim.load_state(str(PROGRAM))
                ip = sim.ips[0]
                target = sim._move_head(ip['ix'], ip['ix_dir'])
                original = sim.grid[target]
                for bit in bits:
                    sim.grid[target] ^= 1 << bit
                before = agent_snapshot(sim)
                for rounds in range(1, 2001):
                    sim.step_all()
                    if sim.grid[target] == original:
                        break
                self.assertEqual(sim.grid[target], original)
                for _ in range(rounds):
                    sim.step_back_all()
                self.assertEqual(agent_snapshot(sim), before)


if __name__ == '__main__':
    unittest.main()
