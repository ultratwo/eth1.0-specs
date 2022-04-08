"""
Ethereum Virtual Machine (EVM) Environmental Instructions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementations of the EVM environment related instructions.
"""

from ethereum.base_types import U256, Uint
from ethereum.utils.numeric import ceil32
from ethereum.utils.safe_arithmetic import u256_safe_add, u256_safe_multiply

from ...state import get_account
from ...utils.address import to_address
from ...vm.error import OutOfGasError
from ...vm.memory import memory_write, touch_memory
from .. import Evm
from ..gas import (
    GAS_BALANCE,
    GAS_BASE,
    GAS_COPY,
    GAS_EXTERNAL,
    GAS_VERY_LOW,
    subtract_gas,
)
from ..stack import pop, push


def address(evm: Evm) -> None:
    """
    Pushes the address of the current executing account to the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, U256.from_be_bytes(evm.message.current_target))

    evm.pc += 1


def balance(evm: Evm) -> None:
    """
    Pushes the balance of the given account onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.StackUnderflowError`
        If `len(stack)` is less than `1`.
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `20`.
    """
    # TODO: There are no test cases against this function. Need to write
    # custom test cases.
    subtract_gas(evm, GAS_BALANCE)

    address = to_address(pop(evm.stack))

    # Non-existent accounts default to EMPTY_ACCOUNT, which has balance 0.
    balance = get_account(evm.env.state, address).balance

    push(evm.stack, balance)

    evm.pc += 1


def origin(evm: Evm) -> None:
    """
    Pushes the address of the original transaction sender to the stack.
    The origin address can only be an EOA.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, U256.from_be_bytes(evm.env.origin))

    evm.pc += 1


def caller(evm: Evm) -> None:
    """
    Pushes the address of the caller onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, U256.from_be_bytes(evm.message.caller))

    evm.pc += 1


def callvalue(evm: Evm) -> None:
    """
    Push the value (in wei) sent with the call onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, evm.message.value)

    evm.pc += 1


def calldataload(evm: Evm) -> None:
    """
    Push a word (32 bytes) of the input data belonging to the current
    environment onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.StackUnderflowError`
        If `len(stack)` is less than `1`.
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `3`.
    """
    subtract_gas(evm, GAS_VERY_LOW)

    # Converting start_index to Uint from U256 as start_index + 32 can
    # overflow U256.
    start_index = Uint(pop(evm.stack))
    value = evm.message.data[start_index : start_index + 32]
    # Right pad with 0 so that there are overall 32 bytes.
    value = value.ljust(32, b"\x00")

    push(evm.stack, U256.from_be_bytes(value))

    evm.pc += 1


def calldatasize(evm: Evm) -> None:
    """
    Push the size of input data in current environment onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, U256(len(evm.message.data)))

    evm.pc += 1


def calldatacopy(evm: Evm) -> None:
    """
    Copy a portion of the input data in current environment to memory.

    This will also expand the memory, in case that the memory is insufficient
    to store the data.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.StackUnderflowError`
        If `len(stack)` is less than `3`.
    """
    # Converting below to Uint as though the start indices may belong to U256,
    # the ending indices may overflow U256.
    memory_start_index = pop(evm.stack)
    data_start_index = pop(evm.stack)
    size = pop(evm.stack)

    words = ceil32(Uint(size)) // 32
    copy_gas_cost = u256_safe_multiply(
        GAS_COPY,
        words,
        exception_type=OutOfGasError,
    )
    total_gas_cost = u256_safe_add(
        GAS_VERY_LOW,
        copy_gas_cost,
        exception_type=OutOfGasError,
    )
    subtract_gas(evm, total_gas_cost)
    touch_memory(evm, memory_start_index, size)

    evm.pc += 1

    if size == 0:
        return

    value = evm.message.data[
        data_start_index : Uint(data_start_index) + Uint(size)
    ]
    # But it is possible that data_start_index + size won't exist in evm.data
    # in which case we need to right pad the above obtained bytes with 0.
    value = value.ljust(size, b"\x00")

    memory_write(evm, memory_start_index, value)


def codesize(evm: Evm) -> None:
    """
    Push the size of code running in current environment onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, U256(len(evm.code)))

    evm.pc += 1


def codecopy(evm: Evm) -> None:
    """
    Copy a portion of the code in current environment to memory.

    This will also expand the memory, in case that the memory is insufficient
    to store the data.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.StackUnderflowError`
        If `len(stack)` is less than `3`.
    """
    # Converting below to Uint as though the start indices may belong to U256,
    # the ending indices may not belong to U256.
    memory_start_index = pop(evm.stack)
    code_start_index = pop(evm.stack)
    size = pop(evm.stack)

    words = ceil32(Uint(size)) // 32
    copy_gas_cost = u256_safe_multiply(
        GAS_COPY,
        words,
        exception_type=OutOfGasError,
    )
    total_gas_cost = u256_safe_add(
        GAS_VERY_LOW,
        copy_gas_cost,
        exception_type=OutOfGasError,
    )
    subtract_gas(evm, total_gas_cost)
    touch_memory(evm, memory_start_index, size)

    evm.pc += 1

    if size == 0:
        return

    value = evm.code[code_start_index : Uint(code_start_index) + Uint(size)]
    # But it is possible that code_start_index + size - 1 won't exist in
    # evm.code in which case we need to right pad the above obtained bytes
    # with 0.
    value = value.ljust(size, b"\x00")

    memory_write(evm, memory_start_index, value)


def gasprice(evm: Evm) -> None:
    """
    Push the gas price used in current environment onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `2`.
    """
    subtract_gas(evm, GAS_BASE)
    push(evm.stack, evm.env.gas_price)

    evm.pc += 1


def extcodesize(evm: Evm) -> None:
    """
    Push the code size of a given account onto the stack.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.StackUnderflowError`
        If `len(stack)` is less than `1`.
    :py:class:`~ethereum.dao_fork.vm.error.OutOfGasError`
        If `evm.gas_left` is less than `20`.
    """
    # TODO: There are no test cases against this function. Need to write
    # custom test cases.
    subtract_gas(evm, GAS_EXTERNAL)

    address = to_address(pop(evm.stack))

    # Non-existent accounts default to EMPTY_ACCOUNT, which has empty code.
    codesize = U256(len(get_account(evm.env.state, address).code))

    push(evm.stack, codesize)

    evm.pc += 1


def extcodecopy(evm: Evm) -> None:
    """
    Copy a portion of an account's code to memory.

    Parameters
    ----------
    evm :
        The current EVM frame.

    Raises
    ------
    :py:class:`~ethereum.dao_fork.vm.error.StackUnderflowError`
        If `len(stack)` is less than `4`.
    """
    # TODO: There are no test cases against this function. Need to write
    # custom test cases.

    address = to_address(pop(evm.stack))
    memory_start_index = pop(evm.stack)
    code_start_index = pop(evm.stack)
    size = pop(evm.stack)

    words = ceil32(Uint(size)) // 32
    copy_gas_cost = u256_safe_multiply(
        GAS_COPY,
        words,
        exception_type=OutOfGasError,
    )
    total_gas_cost = u256_safe_add(
        GAS_EXTERNAL,
        copy_gas_cost,
        exception_type=OutOfGasError,
    )
    subtract_gas(evm, total_gas_cost)
    touch_memory(evm, memory_start_index, size)

    evm.pc += 1

    if size == 0:
        return

    # Non-existent accounts default to EMPTY_ACCOUNT, which has empty code.
    code = get_account(evm.env.state, address).code

    value = code[code_start_index : Uint(code_start_index) + Uint(size)]
    # But it is possible that code_start_index + size won't exist in evm.code
    # in which case we need to right pad the above obtained bytes with 0.
    value = value.ljust(size, b"\x00")

    memory_write(evm, memory_start_index, value)
