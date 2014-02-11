from __future__ import unicode_literals
from future.builtins import bytes, range, chr

from hashlib import sha256
from struct import pack
from collections import namedtuple
from binascii import hexlify
from . import BitcoinEncoding
from .base58 import get_bcaddress
from sys import byteorder


class Input(namedtuple('Input',
                       ['prevout_hash', 'prevout_idx', 'script_sig', 'seqno'])):
    """ Previous hash needs to be given as a byte array in little endian.
    script_sig is a byte string. Others are simply integers. """
    @classmethod
    def coinbase(cls, height, extra_script_sig=b''):
        # Meet BIP 34 by adding the height of the block
        # encode variable length integer
        encoded_height = b''
        length = 0
        while height:
            height, d = divmod(height, 256)
            encoded_height += bytes(pack(str("B"), d))
            length += 1
        sigscript = bytes(pack(str("B"), length)) + encoded_height
        return cls(Transaction._nullprev,
                   4294967295,
                   sigscript + extra_script_sig, 0)


class Output(namedtuple('Output', ['amount', 'script_pub_key'])):
    """ script_pub_key is a byte string. Amount is an integer. """
    @classmethod
    def to_address(cls, amount, address):
        """ Creates an output with a script_pub_key that sends the funds to a
        specific address """
        addr = get_bcaddress(address)
        if addr is None:
            raise ValueError("Invalid address")
        return cls(amount, b'\x76\xa9\x14' + addr + b'\x88\xac')


class Transaction(BitcoinEncoding):
    """ An object wrapper for a bitcoin transaction. More information on the
    raw format at https://en.bitcoin.it/wiki/Transactions. """
    _nullprev = b'\0' * 32

    def __init__(self, raw=None, fees=None):
        # raw transaction data in byte format
        self._raw = bytes(raw)
        self.inputs = []
        self.outputs = []
        self.locktime = 0
        # integer value, not encoded in the pack but for utility
        self.fees = fees
        self.version = 1
        # stored as le bytes
        self._hash = None

    def disassemble(self, raw=None, dump_raw=False, fees=None):
        """ Unpacks a raw transaction into its object components. If raw
        is passed here it will set the raw contents of the object before
        disassembly. Dump raw will mark the raw data for garbage collection
        to save memory. """
        if fees:
            self.fees = fees
        if raw:
            self._raw = bytes(raw)
        data = self._raw

        # first four bytes, little endian unpack
        self.version = self.funpack('<L', data[:4])

        # decode the number of inputs and adjust position counter
        input_count, data = self.varlen_decode(data[4:])

        # loop over the inputs and parse them out
        self.inputs = []
        for i in range(input_count):
            # get the previous transaction hash and it's output index in the
            # previous transaction
            prevout_hash = data[:32]
            prevout_idx = self.funpack('<L', data[32:36])
            # get length of the txn script
            ss_len, data = self.varlen_decode(data[36:])
            script_sig = data[:ss_len]  # get the script
            # get the sequence number
            seqno = self.funpack('<L', data[ss_len:ss_len + 4])

            # chop off the this transaction from the data for next iteration
            # parsing
            data = data[ss_len + 4:]

            # save the input in the object
            self.inputs.append(
                Input(prevout_hash, prevout_idx, script_sig, seqno))

        output_count, data = self.varlen_decode(data)
        self.outputs = []
        for i in range(output_count):
            amount = self.funpack('<Q', data[:8])
            # length of scriptPubKey, parse out
            ps_len, data = self.varlen_decode(data[8:])
            pk_script = data[:ps_len]
            data = data[ps_len:]
            self.outputs.append(
                Output(amount, pk_script))

        self.locktime = self.funpack('<L', data[:4])
        # reset hash to be recacluated on next grab
        self._hash = None
        # ensure no trailing data...
        assert len(data) == 4
        if dump_raw:
            self._raw = None

        return self

    @property
    def is_coinbase(self):
        """ Is the only input from a null prev address, indicating coinbase?
        Technically we could do more checks, but I believe bitcoind doesn't
        check more than the first input being null to count it as a coinbase
        transaction. """
        return self.inputs[0].prevout_hash == self._nullprev

    def assemble(self, split=False):
        """ Reverse of disassemble, pack up the object into a byte string raw
        transaction. split=True will return two halves of the transaction ,
        first chunck will be up until then end of the sigscript, second chunk
        is the remainder. For changing extronance, split off the sigscript """
        data = pack(str('<L'), self.version)
        split_point = None

        data += self.varlen_encode(len(self.inputs))
        for prevout_hash, prevout_idx, script_sig, seqno in self.inputs:
            data += prevout_hash
            data += pack(str('<L'), prevout_idx)
            data += self.varlen_encode(len(script_sig))
            split_point = len(data)
            data += script_sig
            data += pack(str('<L'), seqno)

        data += self.varlen_encode(len(self.outputs))
        for amount, script_pub_key in self.outputs:
            data += pack(str('<Q'), amount)
            data += self.varlen_encode(len(script_pub_key))
            data += script_pub_key

        data += pack(str('<L'), self.locktime)

        self._raw = data
        # reset hash to be recacluated on next grab
        self._hash = None
        if split:
            return data[:split_point], data[split_point:]
        return data

    @property
    def raw(self):
        if self._raw is None:
            self.assemble()
        return self._raw

    @property
    def hash(self):
        """ Compute the hash of the transaction when needed """
        if self._hash is None:
            self._hash = sha256(sha256(self._raw).digest()).digest()
            if byteorder is 'big':  # store in standard le for bitcion
                self._hash = self._hash[::-1]
        return self._hash
    lehash = hash

    @property
    def behash(self):
        return self.hash[::-1]

    @property
    def lehexhash(self):
        return hexlify(self.hash)

    @property
    def behexhash(self):
        return hexlify(self.hash[::-1])

    def __hash__(self):
        return self.funpack('i', self.hash)

    def to_dict(self):
        return {'inputs': [{'prevout_hash': hexlify(inp[0]),
                            'prevout_idx': inp[1],
                            'script_sig': hexlify(inp[2]),
                            'seqno': inp[3]} for inp in self.inputs],
                 'outputs': [{'amount': out[0],
                              'script_pub_key': hexlify(out[1])}
                               for out in self.outputs],
                'data': hexlify(self._raw),
                'locktime': self.locktime,
                'version': self.version,
                'hash': self.lehexhash}