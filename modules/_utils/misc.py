import os, ast, getpass, sys, socket

from .container_docker import CompileDockerJson
#Helper functions  
def load_dependencies(self,root,layer):
    if not os.path.isfile(os.path.join(root,layer,"container-compose.py")):
        return #Don't error out if container-compose.py doesn't exist
    
    with open(os.path.join(root,layer,"container-compose.py")) as fh:
        file=fh.read() 
        
    if os.path.isfile(os.path.join(root,layer,"docker.json")):
        docker_layers, docker_commands =CompileDockerJson(os.path.join(root,layer,"docker.json"))
        file="\n".join(docker_layers+file.splitlines()+docker_commands)
      
    for node in ast.iter_child_nodes(ast.parse(file)):
       if isinstance(node, ast.Expr) and isinstance(node.value,ast.Call):
           function=node.value.func.id
           if function in ["Layer","Base","Env","Shell"]:
               arguments=[eval(ast.unparse(val)) for val in node.value.args]
               getattr(self,function)(*arguments) #Run function

def chroot_command(self,command):
    if self.namespaces['user']:
        result = ["unshare",f"--map-user={self.uid}",f"--map-group={self.gid}","--root=merged",f"--map-auto"]+self.maps #Unshare is available so use it
    else:
        result = ["chroot",f"--userspec={self.uid}:{self.gid}", "merged"] # Unshare does not exist, so use chroot
        
    result+=[f"{self.shell}","-c",f"{self.env}; cd {self.workdir}; {command}"]
    
    
    if self.namespaces['net']:
        result=["sudo","ip","netns","exec",self.netns,"sudo","-u",getpass.getuser()]+result
    
    if sys.platform!="cygwin" and not self.namespaces['user']:
        result=["sudo"]+result
        
    return result
     

    
def is_port_in_use(port) :
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

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
