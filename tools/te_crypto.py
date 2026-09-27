"""Точный порт CTFAK-Native encryption.cpp (проверен против C++-сборки)."""
def rotl1(x): return ((x<<7)|(x>>1))&0xFF

def make_key(title, copyright_, project, magic_char=54):
    blob=(title+copyright_+project).encode('latin1','replace')
    buf=bytearray(256); n=min(len(blob),256); buf[:n]=blob[:n]
    for i in range(128,256): buf[i]=0
    v33=0
    while v33<256 and buf[v33]: v33+=1
    v35=magic_char&0xFF; v34=magic_char&0xFF
    if (v33+1)>0:
        for i in range(v33+1):
            v34=rotl1(v34); buf[i]^=v34
            v35=(v35 + buf[i]*((v34&1)+2))&0xFF
    buf[v33+1]=v35
    return bytes(buf)

def decode_with_key(key, magic_char=54):
    buf=list(range(256))
    mc2=magic_char&0xFF; mc3=magic_char&0xFF
    pos=0; v17=0; v15=True; rtn=False
    for i in range(256):
        mc3=rotl1(mc3)
        if v15: mc2=(mc2 + ((mc3&1)+2)*key[pos])&0xFF
        temp=mc3 ^ key[pos]
        if mc3==key[pos]:
            if v15: rtn = (mc2==key[pos+1])
            mc3=rotl1(magic_char&0xFF); pos=0; v15=False
            temp=mc3 ^ key[0]
        v13=buf[i]; v17=(v17+((temp+v13)&0xFF))&0xFF
        buf[i]=buf[v17]; pos+=1; buf[v17]=v13
    return buf, rtn

class Cipher:
    def __init__(self, title, copyright_, project, magic_char=54):
        self.key=make_key(title,copyright_,project,magic_char)
        self.table,self.valid=decode_with_key(self.key,magic_char)
        self.magic=magic_char
        self._ks=None
    def keystream(self,n):
        if self._ks is None or len(self._ks)<n:
            b=list(self.table); i1=i2=0; out=bytearray(self._ks or b'')
            start=len(out)
            # воссоздаём состояние: проще пересчитать с нуля
            b=list(self.table); i1=i2=0; out=bytearray()
            for _ in range(max(n,4096)):
                i1=(i1+1)&0xFF; v7=b[i1]
                i2=(i2+v7)&0xFF; v9=b[i2]
                b[i1]=v9; b[i2]=v7
                out.append(b[(v7+v9)&0xFF])
            self._ks=bytes(out)
        return self._ks[:n]
    def transform(self, data):
        ks=self.keystream(len(data))
        return bytes(a^b for a,b in zip(data,ks))
