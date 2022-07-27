import os
module_dict={}
module_dict["_utils"+os.sep+"misc.py"]="""
import os, ast, getpass, sys, socket

from .container_docker import CompileDockerJson
#Helper functions  
def load_dependencies(self,root,layer):
    if not os.path.isfile(f\"{root}/{layer}/container-compose.py\"):
        return #Don't error out if container-compose.py doesn't exist
    
    with open(f\"{root}/{layer}/container-compose.py\") as fh:
        file=fh.read() 
        
    if os.path.isfile(f\"{root}/{layer}/docker.json\"):
        docker_layers, docker_commands =CompileDockerJson(open(f\"{root}/{layer}/docker.json\"))
        file=\"\\n\".join(docker_layers+file.splitlines()+docker_commands)
      
    for node in ast.iter_child_nodes(ast.parse(file)):
       if isinstance(node, ast.Expr) and isinstance(node.value,ast.Call):
           function=node.value.func.id
           if function in [\"Layer\",\"Base\",\"Env\",\"Shell\"]:
               arguments=[eval(ast.unparse(val)) for val in node.value.args]
               getattr(self,function)(*arguments) #Run function

def chroot_command(self,command):
    if self.namespaces['user']:
        result = [\"unshare\",f\"--map-user={self.uid}\",f\"--map-group={self.gid}\",\"--root=merged\"] #Unshare is available so use it  
    else:
        result = [\"chroot\",f\"--userspec={self.uid}:{self.gid}\", \"merged\"] # Unshare does not exist, so use chroot
        
    result+=[f\"{self.shell}\",\"-c\",f\"{self.env}; cd {self.workdir}; {command}\"]
    
    
    if self.namespaces['net']:
        result=[\"sudo\",\"ip\",\"netns\",\"exec\",self.netns,\"sudo\",\"-u\",getpass.getuser()]+result
    
    if sys.platform!=\"cygwin\" and not self.namespaces['user']:
        result=[\"sudo\"]+result
        
    return result
     

    
def is_port_in_use(port) :
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def remove_empty_folders_in_diff():
    walk = list(os.walk(\"diff\"))
    for path, _, _ in walk[::-1]:
        if not path.startswith(\"diff/.unionfs\"):
            if len(os.listdir(path)) == 0:
                os.rmdir(path)
def get_all_items(root):
    #Implement Depth-First Search through utils.ROOT
    items=[]
    stack=[root]
    visited={}
    while len(stack)>0:
        v=stack.pop()
        if v not in visited:
            #Visit
            visited[v]=True
            if os.path.isfile(os.path.join(v,\"container-compose.py\")):
                items.append(os.path.relpath(v,root)) #Don't need full path
                continue #No need to search deeper
            if len(os.listdir(v))==1 and os.listdir(v)[0]==\"diff\":
                continue #If there's nothing but diff, no need to search deeper
            
            for w in os.listdir(v):
                w=os.path.join(v,w)
                if w not in visited:
                    stack.append(w)
    return items

           
def str2bool(v):
  return v.lower() in (\"yes\", \"true\", \"t\", \"1\")

"""
module_dict["_utils"+os.sep+"container_docker.py"]="""
#!/usr/bin/env python
import urllib.parse
import tempfile
import sys, os
import re, json
import platform
import subprocess, shutil
# < include '../../utils/utils.py' >
import utils

import shlex
import pathlib
# < include 'requests.py' >
import requests

s = requests.Session()
s.verify=False

import urllib3
urllib3.disable_warnings()

def parse_uri(uri):
    #Convert urls into a proper format:
    if uri.count('/')==0: #Official Docker libraries
        uri='library/' + uri
        
    if uri.count('/')==1: #The default is docker
        uri='index.docker.io/' + uri
        
    #There's no https, we should fix that so urllib can extract things properly
    if urllib.parse.urlparse(uri).netloc=='':
        uri='https://' + uri
    registry=urllib.parse.urlparse(uri).netloc

    image=urllib.parse.urlparse(uri).path
    
    if image.startswith('/'):
        image=image[1:]
    
    if '@' in image: #Support specifying digests with @{digest}
        image=image.split('@')
        image[1]=\"@\"+image[1]
    else:
        image = image.split(':')
    
    if len(image)==1:
        image.append('latest')
    
    image, tag= image
    
    return (registry,image,tag)
#Import from remote docker registry
def Import(uri,path,dockerfile=None):    
    #Make temp folder
    temp_folder=tempfile.mkdtemp()
    
    os.makedirs(path,exist_ok=True)
    
    registry,image,tag=parse_uri(uri)
    #Get registry service and auth service 
    registry_service=\"registry.docker.io\" #By default, use Docker
    auth_service=\"https://auth.docker.io/token\" #By default, use Docker
    
    auth_request=s.get(f\"https://{registry}/v2/\")
    if auth_request.status_code==401:
        if \"WWW-Authenticate\" in auth_request.headers:
            authenticate_header=auth_request.headers[\"WWW-Authenticate\"]
            authenticate_header=authenticate_header.split('\"')
            auth_service = authenticate_header[1]
            try:
                registry_service = authenticate_header[3].replace(\"https://\",\"\").replace(\"http://\",\"\")
            except IndexError:
                registry_service = \"\"
    #Get token
    token=s.get(f\"{auth_service}?service={registry_service}&scope=repository:{image}:pull\").json()['token']
    s.headers['Authorization']=f\"Bearer {token}\"
    s.headers[\"Accept\"]=\"application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json\"
    
    manifest=s.get(f\"https://{registry}/v2/{image}/manifests/{tag.replace('@','sha256:')}\").json() #Digests must be prefixed with the hash algorithm used
    
    #Find the architecture 
    architecture=platform.machine()
    #Handle some Linux distros that report aarch64 instead of amr64
    if architecture=='aarch64':
        architecture=['arm64']
        
    #Handle the arm* family
    elif architecture.startswith('arm'):
        
        #Handle armv6, armv7l, etc.
        if 'v' in architecture:
            architecture=architecture.split('v')
            architecture[1]='v'+architecture[1]
        else:
            architecture=[architecture]
    else:
        architecture=[architecture]
    
    #Get right manifest for architecture
    if 'manifests' in manifest: #Manifest list, so you have to choose the right one
        manifest_list=manifest['manifests']
        
        for manifest in manifest_list:
            if manifest['platform']['architecture']==architecture[0]:
                #Deal with armv* family
                if len(architecture)==2:
                    if manifest['platform']['variant']==architecture[1]:
                        digest=manifest['digest']
                        break
                else:
                    digest=manifest['digest']
                    break
        manifest=s.get(f\"https://{registry}/v2/{image}/manifests/{digest}\").json()
    
    


    #Get information about layers
    layers=manifest[\"layers\"] if \"layers\" in manifest else manifest[\"fsLayers\"] #Support v1
    layers=[_[\"digest\"] if \"digest\" in _ else _[\"blobSum\"] for _ in layers] #Support v1
    
    #Write docker.json to place to be used by CompileDockerJson 
    config=manifest['config']['digest']
    config=s.get(f\"https://{registry}/v2/{image}/blobs/{config}\", headers={\"Accept\":\"application/vnd.docker.container.image.v1+json\"}).json()
    
    #Add layers that will be used by Container.Start
    config['config']['layers']=[os.path.join(registry,_.removeprefix(\"sha256:\")) for _ in layers]
    
    if \"ExposedPorts\" not in config['config']:
        config['config']['ExposedPorts']={}
    
    #Delete all unneccessary keys
    for _ in list(config.keys()):
        if _!=\"config\":
            del config[_]
    config=config['config']
    
    config=json.dumps(config,indent=4).encode('utf-8')
    config_path=pathlib.Path(os.path.join(path,registry,image,tag))
    config_path.mkdir(parents=True, exist_ok=True)
    config_path/=\"docker.json\"
    
    config_path.write_bytes(config)
    
    #Download layers
    for i in range(len(layers)):
        layer_dir=os.path.join(path,registry,layers[i].removeprefix(\"sha256:\"),\"diff\")
        if os.path.isdir(layer_dir):
            continue #If layer exists, don't download it again
        with s.get(f\"https://{registry}/v2/{image}/blobs/{layers[i]}\", stream=True) as r:
            with open(f\"{temp_folder}/layer_{i}.tar.gz\", 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        #urllib.request.urlretrieve(f\"https://{registry}/v2/{image}/blobs/{layers[i]}\", f\"{temp_folder}/layer_{i}.tar.gz\")
        
        
        os.makedirs(layer_dir,exist_ok=True)
        subprocess.run([\"tar\",\"-xf\",f\"{temp_folder}/layer_{i}.tar.gz\",\"-C\",layer_dir])
        os.remove(f\"{temp_folder}/layer_{i}.tar.gz\")
    
    shutil.rmtree(temp_folder)
    

#Convert Dockerfile to Containerfile
def Convert(IN,OUT):
    
                
    stage=\"\" #Stage name
    stages=[]
    def docker_to_container(line): #Convert a line of a Dockerfile into a Container file
        nonlocal stage
        nonlocal stages
        f_strings=[] #Which elements of line should be an f-string
        line=utils.split_string_by_char(line,\" \") #Split string for better parsing
        
        COMMAND=line[0].title() #Get first command
        line=line[1:]
        
        FLAGS={}
        for i in range(len(line)):
            if not line[i].startswith(\"--\"): #If true, then it's the start of the actual command and can be kept
                line=line[i:]
                break
            
            flag=line[i].split('=',1) #Split line by '='
            if len(flag)==1:
                flag.append('') #Pad out the flag array
            flag[0]=flag[0][2:] #Remove the '--'
            FLAGS[flag[0]]=flag[1]
        
        line=list(filter(None,line))
    
        if COMMAND=='From':
            COMMAND=\"Base\"
            if \"AS\" in line: # Multi-stage build
                stage=line[line.index(\"AS\")+1]
                base=line[line.index(\"AS\")-1]
                stages.append(stage)
            else:
                stage=\"\"
                base=line[-1]
                
            base='/'.join(parse_uri(base)) #To fit tags in the traditional Unix directory structure
            
            if stage!=\"\":
                yield f\"{stage}=Container('', {{'temp':''}})\"
                yield f\"{stage}.Init()\"
                yield f\"\"\"{stage}.Base(\"{base}\")\"\"\"
                yield f\"{stage}.Start()\"
                return #Nothing else to do
            else:
                line[-1]=base
        elif COMMAND==\"Arg\": #Not supported
            return \"()\"
        elif COMMAND==\"Run\":
            line=[' '.join(line)]
        elif COMMAND==\"Expose\":
            return 
            COMMAND=\"Port\"
            line.append(line[0]) #Port needs two arguments
        elif COMMAND==\"Copy\":
            if \"from\" in FLAGS:
                if FLAGS[\"from\"] in stages:
                    From=f\"{{{FLAGS['from']}.name}}\"
                    f_strings.append(0)
                else:
                    From=f\"{FLAGS['from']}\"
                line[0]=From+\":\"+line[0].replace(\"{\",\"{{\").replace(\"}\",\"}}\")
        elif COMMAND==\"Cmd\":
            if line[0]==\"[\": #Exec form
                line=line[1:-1]
                line=[_[1:-1] for _ in line] #Remove quotes
                line=[shlex.join(line)]
                    
        #Escape all strings
        line=['\"\"\"'+line[_].replace(\"'\", r'\\'').replace('\"', r'\\\"')+'\"\"\"' for _ in range(len(line))]
        for _ in f_strings:
            line[_]='f'+line[_]
        result=', '.join(line)
        
        #If there is a stage, append commands with the name
        if stage!=\"\":
            COMMAND=stage+\".\"+COMMAND
            
        yield COMMAND+f\"({result})\"
        
    if any(IN.startswith(proto+\"://\") for proto in [\"http\",\"https\"]):
        requests.get(IN).content #Read from disk
    else:
        with open(IN,'r') as f:
            Dockerfile=f.read() #Read from file
    
    #Move all shell line breaks to one line
    Dockerfile=Dockerfile.replace(\"\\\\\\n\",\" \")
    
    with open(os.path.join(OUT,\"Containerfile.py\"),\"w+\") as f:
        Dockerfile=Dockerfile.splitlines()
        
        #Delete last CMD, as this will be the process that runs when container starts
        for i, e in reversed(list(enumerate(Dockerfile))):
            if e.startswith(\"CMD\"):
                del Dockerfile[i]
                break
        for line in Dockerfile:
            line=line.strip()
            line=' '.join(line.split()) #Remove extra spacing
            if not line.startswith('#') and line:
                for result in docker_to_container(line):
                    if result!=\"()\":
                        f.write(result+\"\\n\")
        if stage!=\"\":
            f.write(\"\"\"Copy(f\"{{{}.name}}:/\", \"/\")\\n\"\"\".format(stages[-1])) #The last stage can be named, so if it is, just copy everything from that stage to the actual diff
        for stage in stages:
            f.write(f\"{stage}.Delete()\\n\")

#Convert docker.json into list of commands that can be used by Start
def CompileDockerJson(file):
    layers=[]
    commands=[]
    with open(file,\"rb\") as f:
      config=json.load(f)
    
    for key in config:
        if key==\"layers\":
            for _ in config[key]:
                layers.append(f\"Layer('{_}')\")
        elif key==\"Env\":
            for _ in config[key]:
                layers.append(f\"\"\"Env(\\\"\\\"\\\"{_}\\\"\\\"\\\")\"\"\")
        elif key==\"WorkingDir\":
            commands.append(f\"Workdir('{config[key]}')\")
        elif key==\"ExposedPorts\":
            pass
            for _ in config[key]:
                _=_.split(\"/\")[0]
                commands.append(f\"Port({_},{_})\")
    command=[]
    if 'Cmd' in config:
        command=config['Cmd']
    if 'Entrypoint' in config:
        command=config['Entrypoint']+command
    commands.append(f\"\"\"Run(\\\"\\\"\\\"{shlex.join(command)}\\\"\\\"\\\")\"\"\")
    return layers, commands

"""
module_dict["_utils"+os.sep+"__init__.py"]="""
from .container_docker import Convert
from .misc import *


"""

import os
import types
import zipfile
import sys
import io
import json

class ZipImporter(object):
    def __init__(self, zip_file):
        self.zfile = zip_file
        self._paths = [x.filename for x in self.zfile.filelist]
        
    def _mod_to_paths(self, fullname):
        # get the python module name
        py_filename = fullname.replace(".", os.sep) + ".py"
        # get the filename if it is a package/subpackage
        py_package = fullname.replace(".", os.sep) + os.sep + "__init__.py"
        if py_filename in self._paths:
            return py_filename
        elif py_package in self._paths:
            return py_package
        else:
            return None

    def find_module(self, fullname, path):
        if self._mod_to_paths(fullname) is not None:
            return self
        return None

    def load_module(self, fullname):
        filename = self._mod_to_paths(fullname)
        if not filename in self._paths:
            raise ImportError(fullname)
        new_module = types.ModuleType(fullname)
        sys.modules[fullname]=new_module
        if filename.endswith("__init__.py"):
            new_module.__path__ = [] 
            new_module.__package__ = fullname
        else:
            new_module.__package__ = fullname.rpartition('.')[0]
        exec(self.zfile.open(filename, 'r').read(),new_module.__dict__)
        new_module.__file__ = filename
        new_module.__loader__ = self
        new_module.__spec__=json.__spec__ # To satisfy importlib._common.get_package
        return new_module

module_zip=zipfile.ZipFile(io.BytesIO(),"w")
for key in module_dict:
    module_zip.writestr(key,module_dict[key])

module_importer=ZipImporter(module_zip)
sys.meta_path.insert(0,module_importer)

#from _utils import *
import _utils
globals().update(_utils.__dict__)
    
if module_importer in sys.meta_path:
    sys.meta_path.remove(module_importer)

#for key in sys.modules.copy():
#    if key=="_utils" or key.startswith("_utils."):
#        del sys.modules[key]
