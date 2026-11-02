"""Shared guard for the offline tests: block hardware imports, network connections and DNS lookups."""
import importlib.abc
import socket
import sys

BANNED = ("science_jubilee.Machine", "science_jubilee.Experiment",
          "science_jubilee.tools", "oceandirect", "OceanDirect", "opentrons")


class BlockHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in BANNED):
            raise RuntimeError(f"Offline test attempted to import hardware module: {fullname}")
        return None


def block_network(*args, **kwargs):
    raise RuntimeError("Offline test attempted a network connection or DNS lookup.")


def install():
    sys.meta_path.insert(0, BlockHardware())
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    socket.create_connection = block_network
    socket.getaddrinfo = block_network


def loaded_hardware_modules():
    return [name for name in sys.modules if any(name == b or name.startswith(b + ".") for b in BANNED)]
