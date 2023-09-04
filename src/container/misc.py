import os, getpass, sys, socket
import random, string

import utils

from .docker import CompileDockerJson
#Helper functions  

def chroot_command(self,command):
    if self.namespaces['user']:
        result = ["unshare",f"--map-user={self.uid}",f"--map-group={self.gid}","--root=merged"]+self.maps #Unshare is available so use it
    else:
        result = ["chroot",f"--userspec={self.uid}:{self.gid}", "merged"] # Unshare does not exist, so use chroot
    if not self.shell: #No shell, so can't cd
        if self.namespaces['user']: #Plain chroot doesn't have this option
            result.extend([f'--wd={self.workdir}',command])
        result=['env',' '.join(self.env)]+result
    else:
        result.extend([f"{self.shell}","-c",f"{utils.env_list_to_string(self.env)}; cd {self.workdir}; {command}"])

    if self.namespaces['net']:
        result=["sudo","ip","netns","exec",self.netns,"sudo","-u",getpass.getuser()]+result
    
    if sys.platform!="cygwin" and not self.namespaces['user']: #Cygwin doesn't have chroot
        result=["sudo"]+result
    return result
     
def convert_colon_string_to_directory(self,string):
    string=utils.split_string_by_char(string,char=":")
    if string[0]=="root":
        string=string[1] #The directory is just the absolute path in the host
    elif len(string)==1:
        string=string[0] # No container was specified, so assume "root"
    else:
        string=f"{self.ROOT}/{string[0]}/diff{string[1]}" # Container was specified, so use it
    string=os.path.expanduser(string)
    return string
    
def is_port_in_use(source):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((source[0], int(source[1]))) == 0

def remove_empty_folders_in_diff():
    walk = list(os.walk("diff"))
    for path, _, _ in walk[::-1]:
        if not path.startswith("diff/.unionfs"):
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
            if os.path.isfile(os.path.join(v,"container-compose.py")):
                items.append(os.path.relpath(v,root)) #Don't need full path
                continue #No need to search deeper
            if len(os.listdir(v))==1 and os.listdir(v)[0]=="diff":
                continue #If there's nothing but diff, no need to search deeper
            
            for w in os.listdir(v):
                w=os.path.join(v,w)
                if w not in visited:
                    stack.append(w)
    return items

           
def str2bool(v):
  return v.lower() in ("yes", "true", "t", "1")

def generate_random_string(N):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(N))