"""Read-only PE inspection, inspired by ChatTrace (MIT); see NOTICE.

Unlike a guessed prologue offset, function bounds come from PE x64 exception
metadata (.pdata). Location alone is NOT proof of key-capture compatibility.
"""
import hashlib
import json
from pathlib import Path
import re
import struct


def locate(dll):
    import pefile
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    path = Path(dll)
    raw = path.read_bytes()
    pe = pefile.PE(data=raw, fast_load=True)
    if pe.FILE_HEADER.Machine != 0x8664:
        raise ValueError('仅检查 x64 Weixin.dll')
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_EXCEPTION']])
    text = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.text')
    rdata = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.rdata')
    strings = {rdata.VirtualAddress + m.start() for m in re.finditer(b'MMV1', rdata.get_data())}
    code = text.get_data()
    refs = []
    # An exact x64 RIP-relative lea/mov instruction, then validate via disassembler.
    for match in re.finditer(rb'[\x40-\x4f][\x8d\x8b][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', code):
        i = match.start()
        if i + 7 > len(code): continue
        rva = text.VirtualAddress + i
        target = rva + 7 + struct.unpack_from('<i', code, i+3)[0]
        if target in strings: refs.append(rva)
    bounds = [(e.struct.BeginAddress,e.struct.EndAddress) for e in getattr(pe,'DIRECTORY_ENTRY_EXCEPTION',[])]
    dis = Cs(CS_ARCH_X86,CS_MODE_64)
    candidates = []
    for ref in refs:
        matches = [(start,end) for start,end in bounds if start <= ref < end]
        if len(matches)!=1:
            continue
        start,end = matches[0]
        insns = list(dis.disasm(pe.get_data(start,end-start),start))
        if not any(i.address==ref for i in insns):
            continue
        candidates.append({'entry_rva':start,'end_rva':end,'mmv1_reference_rva':ref,
                           'entry_bytes':pe.get_data(start,32).hex(),
                           'instructions':[f'{i.address:x}: {i.mnemonic} {i.op_str}' for i in insns[:45]],
                           'reference_instructions':[f'{i.address:x}: {i.mnemonic} {i.op_str}' for i in insns if ref-24<=i.address<=ref+24]})
    return {'dll':str(path.resolve()),'dll_sha256':hashlib.sha256(raw).hexdigest(),
            'method':'MMV1 RIP reference + PE exception function bounds + x64 instruction validation',
            'candidates':candidates,'runtime_key_capture_verified':False}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('dll');p.add_argument('--output',type=Path)
    args=p.parse_args();result=locate(args.dll)
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: args.output.write_text(output,encoding='utf-8')
    print(output)
