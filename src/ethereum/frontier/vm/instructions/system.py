"""
Ethereum Virtual Machine (EVM) System Instructions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Implementations of the EVM system related instructions.
"""
from ethereum.base_types import U256, Bytes0, Uint
from ethereum.utils.safe_arithmetic import u256_safe_add

from ...state import (
    account_has_code_or_nonce,
    get_account,
    increment_nonce,
    set_account_balance,
)
from ...utils.address import compute_contract_address, to_address
from ...vm.error import OutOfGasError
from .. import Evm, Message
from ..gas import (
    GAS_CREATE,
    GAS_ZERO,
    calculate_call_gas_cost,
    calculate_message_call_gas_stipend,
    subtract_gas,
)
from ..memory import memory_read_bytes, memory_write, touch_memory
from ..stack import pop, push


def create(evm: Evm) -> None:
    """
    Creates a new account with associated code.

    Parameters
    ----------
    evm :
        The current EVM frame.
    """
    # This import causes a circular import error
    # if it's not moved inside this method
    from ...vm.interpreter import STACK_DEPTH_LIMIT, process_create_message

    endowment = pop(evm.stack)
    memory_start_position = pop(evm.stack)
    memory_size = pop(evm.stack)

    subtract_gas(evm, GAS_CREATE)
    touch_memory(evm, memory_start_position, memory_size)

    call_data = memory_read_bytes(evm, memory_start_position, memory_size)

    sender_address = evm.message.current_target
    sender = get_account(evm.env.state, sender_address)

    evm.pc += 1

    if sender.balance < endowment:
        push(evm.stack, U256(0))
        return None

    if sender.nonce == Uint(2**64 - 1):
        push(evm.stack, U256(0))
        return None

    if evm.message.depth + 1 > STACK_DEPTH_LIMIT:
        push(evm.stack, U256(0))
        return None

    increment_nonce(evm.env.state, evm.message.current_target)

    create_message_gas = evm.gas_left
    subtract_gas(evm, create_message_gas)

    contract_address = compute_contract_address(
        evm.message.current_target,
        get_account(evm.env.state, evm.message.current_target).nonce - U256(1),
    )
    is_collision = account_has_code_or_nonce(evm.env.state, contract_address)
    if is_collision:
        push(evm.stack, U256(0))
        return

    child_message = Message(
        caller=evm.message.current_target,
        target=Bytes0(),
        gas=create_message_gas,
        value=endowment,
        data=b"",
        code=call_data,
        current_target=contract_address,
        depth=evm.message.depth + 1,
        code_address=None,
    )
    child_evm = process_create_message(child_message, evm.env)
    evm.children.append(child_evm)
    if child_evm.has_erred:
        push(evm.stack, U256(0))
    else:
        evm.logs += child_evm.logs
        push(evm.stack, U256.from_be_bytes(child_evm.message.current_target))
    evm.gas_left = child_evm.gas_left
    child_evm.gas_left = U256(0)


def return_(evm: Evm) -> None:
    """
    Halts execution returning output data.

    Parameters
    ----------
    evm :
        The current EVM frame.
    """
    memory_start_position = pop(evm.stack)
    memory_size = pop(evm.stack)

    subtract_gas(evm, GAS_ZERO)
    touch_memory(evm, memory_start_position, memory_size)

    evm.output = memory_read_bytes(evm, memory_start_position, memory_size)
    # HALT the execution
    evm.running = False


def call(evm: Evm) -> None:
    """
    Message-call into an account.

    Parameters
    ----------
    evm :
        The current EVM frame.
    """
    from ...vm.interpreter import STACK_DEPTH_LIMIT, process_message

    gas = pop(evm.stack)
    to = to_address(pop(evm.stack))
    value = pop(evm.stack)
    memory_input_start_position = pop(evm.stack)
    memory_input_size = pop(evm.stack)
    memory_output_start_position = pop(evm.stack)
    memory_output_size = pop(evm.stack)

    touch_memory(evm, memory_input_start_position, memory_input_size)
    touch_memory(evm, memory_output_start_position, memory_output_size)

    call_data = memory_read_bytes(
        evm, memory_input_start_position, memory_input_size
    )

    call_gas_fee = calculate_call_gas_cost(evm.env.state, gas, to, value)
    message_call_gas_fee = u256_safe_add(
        gas,
        calculate_message_call_gas_stipend(value),
        exception_type=OutOfGasError,
    )
    subtract_gas(evm, call_gas_fee)

    sender_balance = get_account(
        evm.env.state, evm.message.current_target
    ).balance

    evm.pc += 1

    if sender_balance < value:
        push(evm.stack, U256(0))
        evm.gas_left += message_call_gas_fee
        return None
    if evm.message.depth + 1 > STACK_DEPTH_LIMIT:
        push(evm.stack, U256(0))
        evm.gas_left += message_call_gas_fee
        return None

    code = get_account(evm.env.state, to).code
    child_message = Message(
        caller=evm.message.current_target,
        target=to,
        gas=message_call_gas_fee,
        value=value,
        data=call_data,
        code=code,
        current_target=to,
        depth=evm.message.depth + 1,
        code_address=to,
    )
    child_evm = process_message(child_message, evm.env)
    evm.children.append(child_evm)

    if child_evm.has_erred:
        push(evm.stack, U256(0))
    else:
        evm.logs += child_evm.logs
        push(evm.stack, U256(1))

    actual_output_size = min(memory_output_size, U256(len(child_evm.output)))
    memory_write(
        evm,
        memory_output_start_position,
        child_evm.output[:actual_output_size],
    )
    evm.gas_left += child_evm.gas_left
    child_evm.gas_left = U256(0)


def callcode(evm: Evm) -> None:
    """
    Message-call into this account with alternative account’s code.

    Parameters
    ----------
    evm :
        The current EVM frame.
    """
    from ...vm.interpreter import STACK_DEPTH_LIMIT, process_message

    gas = pop(evm.stack)
    code_address = to_address(pop(evm.stack))
    value = pop(evm.stack)
    memory_input_start_position = pop(evm.stack)
    memory_input_size = pop(evm.stack)
    memory_output_start_position = pop(evm.stack)
    memory_output_size = pop(evm.stack)
    to = evm.message.current_target

    touch_memory(evm, memory_input_start_position, memory_input_size)
    touch_memory(evm, memory_output_start_position, memory_output_size)

    call_data = memory_read_bytes(
        evm, memory_input_start_position, memory_input_size
    )

    call_gas_fee = calculate_call_gas_cost(evm.env.state, gas, to, value)
    message_call_gas_fee = u256_safe_add(
        gas,
        calculate_message_call_gas_stipend(value),
        exception_type=OutOfGasError,
    )
    subtract_gas(evm, call_gas_fee)

    sender_balance = get_account(
        evm.env.state, evm.message.current_target
    ).balance

    evm.pc += 1

    if sender_balance < value:
        push(evm.stack, U256(0))
        evm.gas_left += message_call_gas_fee
        return None
    if evm.message.depth + 1 > STACK_DEPTH_LIMIT:
        push(evm.stack, U256(0))
        evm.gas_left += message_call_gas_fee
        return None

    code = get_account(evm.env.state, code_address).code
    child_message = Message(
        caller=evm.message.current_target,
        target=to,
        gas=message_call_gas_fee,
        value=value,
        data=call_data,
        code=code,
        current_target=to,
        depth=evm.message.depth + 1,
        code_address=code_address,
    )

    child_evm = process_message(child_message, evm.env)
    evm.children.append(child_evm)
    if child_evm.has_erred:
        push(evm.stack, U256(0))
    else:
        evm.logs += child_evm.logs
        push(evm.stack, U256(1))
    actual_output_size = min(memory_output_size, U256(len(child_evm.output)))
    memory_write(
        evm,
        memory_output_start_position,
        child_evm.output[:actual_output_size],
    )
    evm.gas_left += child_evm.gas_left
    child_evm.gas_left = U256(0)


def selfdestruct(evm: Evm) -> None:
    """
    Halt execution and register account for later deletion.

    Parameters
    ----------
    evm :
        The current EVM frame.
    """
    beneficiary = to_address(pop(evm.stack))
    originator = evm.message.current_target
    beneficiary_balance = get_account(evm.env.state, beneficiary).balance
    originator_balance = get_account(evm.env.state, originator).balance

    # First Transfer to beneficiary
    set_account_balance(
        evm.env.state, beneficiary, beneficiary_balance + originator_balance
    )
    # Next, Zero the balance of the address being deleted (must come after
    # sending to beneficiary in case the contract named itself as the
    # beneficiary).
    set_account_balance(evm.env.state, originator, U256(0))

    # register account for deletion
    evm.accounts_to_delete.add(originator)

    # HALT the execution
    evm.running = False
