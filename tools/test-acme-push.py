#!/usr/bin/env python3
"""acme push: plexmon's cryptography checked against the test vectors of the
protocol (section 8) and the RFCs - with the `cryptography` package where it
is installed, and in plain Python.

    python3 tools/test-acme-push.py            (from the unpacked package)
    python3 tools/test-acme-push.py /usr/local/lib/plexmon
"""
import os
import sys
import base64
import hashlib

HERE = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import plexmon_admin as E  # noqa: E402

def run(label):
    seed=bytes(range(32)); appk=bytes(range(32,64)); eph=bytes(range(64,96)); gn=bytes(range(96,108))
    APP='00112233445566778899aabbccddeeff'; body=b'{"slots": ["auto"]}'; fp='ab'*32
    pub=E.ed25519_public(seed)
    assert base64.b64encode(pub).decode()=='A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=', base64.b64encode(pub)
    kid=E.push_key_id(pub); assert kid=='56475aa75463474c'
    msg=E.push_canonical('inventory',APP,kid,1793534400,'n0nce-0123456789ab',body)
    assert hashlib.sha256(body).hexdigest()=='d3a920a683e4d42e6f5a6971cb03c2fd4361964436db5f7460c4a638836222d8'
    sig=E.ed25519_sign(seed,msg)
    assert base64.b64encode(sig).decode()=='7nqJW4/NR3Et6pZx3B8qS3rvQGaQR0UTlNdptpRs4lxaZtc3Cf2zBRkOWPB0tV/rmQNfwbcl2/FKJ3Pu2h/bDg=='
    assert E.ed25519_verify(pub,msg,sig); assert not E.ed25519_verify(pub,msg+b'x',sig)
    bad=bytearray(sig); bad[5]^=1; assert not E.ed25519_verify(pub,msg,bytes(bad))
    apub=E.x25519_public(appk); assert base64.b64encode(apub).decode()=='NYBy1jZYgNGu6jKa35EhODhR7SGijjt16WXQ0s0WYlQ='
    sk=E.push_seal_key('KEY\n',APP,apub,fp,eph=eph,nonce=gn)
    assert sk['epk']=='eaYx7t4b+cmPEgMs3q3Q56B5OY/HhriMyEbsia+FpRo=', sk
    assert E.x25519(eph,apub).hex()=='04c304fb1ca83cee75e206344231f33797e07d9929db670994b7c6fbeb1dc255'
    assert sk['nonce']=='YGFiY2RlZmdoaWpr' and sk['ct']=='2UbSrj9NwnCBu3qZW4U7MzxzlGw=', sk
    epk=base64.b64decode(sk['epk']); sh=E.x25519(appk,epk)
    aes=E.hkdf_sha256(sh,epk+apub,b'acme-push/1 key_pem'); assert aes.hex()=='14067e8e830df01ae120ce73359b5f01e9d26931fe6ecdfa4d1a00fb9fe2e7b0'
    pt=E.aes256gcm_open(aes,gn,base64.b64decode(sk['ct']),('acme-push/1 key\n%s\n%s'%(APP,fp)).encode()); assert pt==b'KEY\n'
    assert E.aes256gcm_open(aes,gn,base64.b64decode(sk['ct']),b'other') is None
    assert E.push_code_hmac('ABCD-EFGH-JKMN-PQRS','pair',APP,kid,'n0nce-0123456789ab')=='e8e1978c03896460ce7f6e1697d5a5220c8f3a1cd8a01d2fc2e838dbd9318b39'
    # FIPS-197 C.3 and RFC 7748 5.2
    assert E._Aes256Push(bytes(range(32))).block(bytes.fromhex('00112233445566778899aabbccddeeff')).hex()=='8ea2b7ca516745bfeafc49904b496089'
    assert E.x25519(bytes.fromhex('a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4'),bytes.fromhex('e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c')).hex()=='c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552'
    # longer GCM vs both ways
    k=os.urandom(32); n=os.urandom(12); p=os.urandom(3000); a=os.urandom(77)
    c=E.aes256gcm_seal(k,n,p,a); assert E.aes256gcm_open(k,n,c,a)==p
    print(label, 'OK')
    return c,k,n,p,a
have = E.HAVE_CRYPTOGRAPHY
if have:
    run('with cryptography:')
E.HAVE_CRYPTOGRAPHY = False
run('in plain Python:')
E.HAVE_CRYPTOGRAPHY = have
print('acme push: every vector matches')
