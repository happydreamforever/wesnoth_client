"""Real ABI-preserving stock-engine strict TLS socket fixture (explicit opt-in)."""
import gzip,hashlib,json,os,socket,ssl,struct,subprocess,sys,threading,time,unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get('ALLIANCE_SHARED_CONNECTION_PROBE'), 'Requires isolated real-engine strict socket probe')
class SharedConnection(unittest.TestCase):
 def test_strict_transport(self):
  sys.path.insert(0,os.environ['ALLIANCE_SHARED_SERVER_SOURCE'])
  from tools.alliance_transport import prepare_identity
  root=Path(os.environ['ALLIANCE_SHARED_CONNECTION_SCRATCH']);root.mkdir(parents=True,exist_ok=False)
  profile,_=prepare_identity(root/'identity','127.0.0.1',15002,Path('/usr/bin/openssl'))
  other,_=prepare_identity(root/'other','127.0.0.1',15002,Path('/usr/bin/openssl'))
  binary=os.environ['ALLIANCE_SHARED_CONNECTION_PROBE'];results=[]
  def read(s,n):
   result=b''
   while len(result)<n:
    more=s.recv(n-len(result))
    if not more:raise EOFError()
    result+=more
   return result
  def one(name,action,version=None,identity='identity',mode='reject',expected_time=None):
   server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);server.bind(('127.0.0.1',15002));server.listen(1);server.settimeout(15)
   errors=[]
   def serve():
    try:
     with server:
      conn,_=server.accept()
      with conn:
       conn.settimeout(25);self.assertEqual(read(conn,4),struct.pack('!I',1))
       if name=='downgrade':conn.sendall(b'\xff'*4);return
       if name=='preamble-eof':return
       if name=='preamble-stall':time.sleep(11);return
       conn.sendall(b'\0'*4)
       context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/identity/'server-cert.pem',root/identity/'server-key.pem')
       if version:context.minimum_version=context.maximum_version=version
       try:
        with context.wrap_socket(conn,server_side=True) as secure:action(secure)
       except (ssl.SSLError,ConnectionError,EOFError):pass
    except Exception as e:errors.append(type(e).__name__)
   thread=threading.Thread(target=serve);thread.start();start=time.monotonic()
   result=subprocess.run([binary,'--connection',str(root/'identity/public-profile.json'),mode],capture_output=True,text=True,timeout=30)
   elapsed=time.monotonic()-start;thread.join(30)
   (root/(name+'.log')).write_text(result.stdout+result.stderr)
   self.assertFalse(thread.is_alive());self.assertFalse(errors,(errors,result.stdout,result.stderr))
   self.assertEqual(result.returncode,0,(name,result.stdout,result.stderr))
   if expected_time:self.assertLess(elapsed,expected_time)
   results.append({'case':name,'exit':result.returncode,'seconds':round(elapsed,3)})
  def echo(s):
   size=struct.unpack('!I',read(s,4))[0];body=read(s,size);self.assertIn(b'safe fixture payload',gzip.decompress(body));s.sendall(struct.pack('!I',size)+body);time.sleep(.1)
  def packet(body):return lambda s:s.sendall(struct.pack('!I',len(body))+body)
  one('tls12',echo,ssl.TLSVersion.TLSv1_2,mode='echo')
  one('tls13',echo,ssl.TLSVersion.TLSv1_3,mode='echo')
  one('untrusted',lambda s:None,identity='other')
  one('downgrade',lambda s:None)
  one('preamble-eof',lambda s:None)
  one('preamble-stall',lambda s:None,expected_time=13)
  one('zero-frame',lambda s:s.sendall(b'\0'*4))
  one('oversize',lambda s:s.sendall(struct.pack('!I',65537)))
  one('invalid-gzip',packet(b'not gzip'))
  one('decompression-limit',packet(gzip.compress(b'x'*(2*1024*1024+1))))
  one('trailing-gzip',packet(gzip.compress(b'[probe]\nvalue="safe fixture payload"\n[/probe]\n')+b'garbage'))
  one('partial-header',lambda s:s.sendall(b'\0\0'))
  one('partial-body',lambda s:s.sendall(struct.pack('!I',20)+b'a'))
  one('cancel',lambda s:time.sleep(.3),mode='cancel')
  (root/'validation.json').write_text(json.dumps({'status':'passed','cases':results},indent=2))

if __name__=='__main__':unittest.main()
