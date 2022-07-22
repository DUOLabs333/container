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
    registry_service="registry.docker.io" #By default, use Docker
    auth_service="https://auth.docker.io/token" #By default, use Docker
    
    auth_request=s.get(f"https://{registry}/v2/")
    if auth_request.status_code==401:
        if "WWW-Authenticate" in auth_request.headers:
            authenticate_header=auth_request.headers["WWW-Authenticate"]
            authenticate_header=authenticate_header.split('"')
            auth_service = authenticate_header[1]
            try:
                registry_service = authenticate_header[3].replace("https://","").replace("http://","")
            except IndexError:
                registry_service = ""
    #Get token
    token=s.get(f"{auth_service}?service={registry_service}&scope=repository:{image}:pull").json()['token']
    s.headers['Authorization']=f"Bearer {token}"
    s.headers["Accept"]="application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"
    
    manifest=s.get(f"https://{registry}/v2/{image}/manifests/{tag}").json()
    
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
        manifest=s.get(f"https://{registry}/v2/{image}/manifests/{digest}").json()
    
    


    #Get information about layers
    layers=manifest["layers"] if "layers" in manifest else manifest["fsLayers"] #Support v1
    layers=[_["digest"] if "digest" in _ else _["blobSum"] for _ in layers] #Support v1
    
    #Write docker.json to place to be used by CompileDockerJson 
    config=manifest['config']['digest']
    config=s.get(f"https://{registry}/v2/{image}/blobs/{config}", headers={"Accept":"application/vnd.docker.container.image.v1+json"}).json()
    
    #Add layers that will be used by Container.Start
    config['rootfs']['layers']=[os.path.join(registry,_.removeprefix("sha256:")) for _ in layers]
    
    config=json.dumps(config).encode('utf-8')
    config_path=pathlib.Path(os.path.join(path,registry,image,tag))
    config_path.mkdir(parents=True, exist_ok=True)
    config_path/="docker.json"
    
    config_path.write_bytes(config)
    
    #Download layers
    for i in range(len(layers)):
        layer_dir=os.path.join(path,registry,layers[i].removeprefix("sha256:"),"diff")
        if os.path.isdir(layer_dir):
            continue #If layer exists, don't download it again
        with s.get(f"https://{registry}/v2/{image}/blobs/{layers[i]}", stream=True) as r:
            with open(f"{temp_folder}/layer_{i}.tar.gz", 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        #urllib.request.urlretrieve(f"https://{registry}/v2/{image}/blobs/{layers[i]}", f"{temp_folder}/layer_{i}.tar.gz")
        
        
        os.makedirs(layer_dir,exist_ok=True)
        subprocess.run(["tar","-xf",f"{temp_folder}/layer_{i}.tar.gz","-C",layer_dir])
        os.remove(f"{temp_folder}/layer_{i}.tar.gz")
    
    shutil.rmtree(temp_folder)
    

#Convert Dockerfile to Containerfile
def Convert(IN,OUT):
    
                
    stage="" #Stage name
    stages=[]
    def docker_to_container(line): #Convert a line of a Dockerfile into a Container file
        nonlocal stage
        nonlocal stages
        f_strings=[] #Which elements of line should be an f-string
        line=utils.split_string_by_char(line," ") #Split string for better parsing
        
        COMMAND=line[0].title() #Get first command
        line=line[1:]
        
        FLAGS={}
        for i in range(len(line)):
            if not line[i].startswith("--"): #If true, then it's the start of the actual command and can be kept
                line=line[i:]
                break
            
            flag=line[i].split('=',1) #Split line by '='
            if len(flag)==1:
                flag.append('') #Pad out the flag array
            flag[0]=flag[0][2:] #Remove the '--'
            FLAGS[flag[0]]=flag[1]
        
        line=list(filter(None,line))
    
        if COMMAND=='From':
            COMMAND="Base"
            if "AS" in line: # Multi-stage build
                stage=line[line.index("AS")+1]
                base=line[line.index("AS")-1]
                stages.append(stage)
            else:
                stage=""
                base=line[-1]
                
            base='/'.join(parse_uri(base)) #To fit tags in the traditional Unix directory structure
            
            if stage!="":
                yield f"{stage}=Container('', {{'temp':''}})"
                yield f"{stage}.Init()"
                yield f"""{stage}.Base("{base}")"""
                yield f"{stage}.Start()"
                return #Nothing else to do
            else:
                line[-1]=base
        elif COMMAND=="Arg": #Not supported
            return "()"
        elif COMMAND=="Run":
            line=[' '.join(line)]
        elif COMMAND=="Expose":
            return 
            COMMAND="Port"
            line.append(line[0]) #Port needs two arguments
        elif COMMAND=="Copy":
            if "from" in FLAGS:
                if FLAGS["from"] in stages:
                    From=f"{{{FLAGS['from']}.name}}"
                    f_strings.append(0)
                else:
                    From=f"{FLAGS['from']}"
                line[0]=From+":"+line[0].replace("{","{{").replace("}","}}")
        elif COMMAND=="Cmd":
            if line[0]=="[": #Exec form
                line=line[1:-1]
                line=[_[1:-1] for _ in line] #Remove quotes
                line=[shlex.join(line)]
                    
        #Escape all strings
        line=['"""'+line[_].replace("'", r'\'').replace('"', r'\"')+'"""' for _ in range(len(line))]
        for _ in f_strings:
            line[_]='f'+line[_]
        result=', '.join(line)
        
        #If there is a stage, append commands with the name
        if stage!="":
            COMMAND=stage+"."+COMMAND
            
        yield COMMAND+f"({result})"
        
    if any(IN.startswith(proto+"://") for proto in ["http","https"]):
        requests.get(IN).content #Read from disk
    else:
        with open(IN,'r') as f:
            Dockerfile=f.read() #Read from file
    
    #Move all shell line breaks to one line
    Dockerfile=Dockerfile.replace("\\\n"," ")
    
    with open(os.path.join(OUT,"Containerfile.py"),"w+") as f:
        Dockerfile=Dockerfile.splitlines()
        
        #Delete last CMD, as this will be the process that runs when container starts
        for i, e in reversed(list(enumerate(Dockerfile))):
            if e.startswith("CMD"):
                del Dockerfile[i]
                break
        for line in Dockerfile:
            line=line.strip()
            line=' '.join(line.split()) #Remove extra spacing
            if not line.startswith('#') and line:
                for result in docker_to_container(line):
                    if result!="()":
                        f.write(result+"\n")
        if stage!="":
            f.write("""Copy(f"{{{}.name}}:/", "/")\n""".format(stages[-1])) #The last stage can be named, so if it is, just copy everything from that stage to the actual diff
        for stage in stages:
            f.write(f"{stage}.Delete()\n")

#Convert docker.json into list of commands that can be used by Start
def CompileDockerJson(file):
    commands=[]
    with open(file,"rb") as f:
      config=json.load(f)
    
    for _ in config['rootfs']['layers']:
        commands.append(f"Layer('{_}')")
    
    for _ in config['config']['Env']:
        commands.append(f"""Env(\"\"\"{_}\"\"\")""")
    
    commands.append(f"Workdir('{config['config']['WorkingDir']}')")
    
    for _ in config['config']['ExposedPorts']:
        _=_.split("/")[0]
        commands.append(f"Port({_},{_})")
    
    commands.append(f"""Run(\"\"\"{shlex.join(config['config']['Cmd'])}\"\"\")""")
    return '\n'.join(commands)     #At the end, join them by \n