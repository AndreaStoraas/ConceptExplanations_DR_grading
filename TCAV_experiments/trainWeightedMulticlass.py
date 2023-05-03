#Try to train a multiclass classifier for DR:
#However, since the dataset in imbalanced, I use the WeightedRandomSampler
import os
import random
import argparse
import torch
import copy

import numpy as np
from torch import functional
import torch.nn as nn

from sklearn import metrics

from torchvision import datasets, models, transforms
from torch.utils.data import DataLoader
#Import the WeightedRandomSampler:
from torch.utils.data.sampler import WeightedRandomSampler
#And import albumentations for CLAHE preprocessing:
import cv2 as cv
import albumentations as albu
from albumentations.pytorch import ToTensorV2
#Must customize the dataset to use albumentations...
from torch.utils.data import Dataset as BaseDataset


random.seed(0)
np.random.seed(0)
#Should use torch.manual_seed: https://pytorch.org/vision/stable/transforms.html
torch.manual_seed(0)

argument_parser = argparse.ArgumentParser(description="")


# Hardware
# Should us ID = 0 (Vajira will use ID = 1)
argument_parser.add_argument("--device_id", type=int, default=0, help="")
argument_parser.add_argument("-d", "--data_path", required=True, type=str)
argument_parser.add_argument("-o", "--output_path", type=str, default="output")
argument_parser.add_argument("-c","--n_classes",type=int,default=5) #DR grading goes from 0 to 4
argument_parser.add_argument("-e", "--epochs", type=int, default=100)


args = argument_parser.parse_args()

#Device:
torch.cuda.set_device(args.device_id)
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

#=========================================
# Helper functions and Datasets
#=========================================
#Define dataset class (necessary when applying albumentations transformations):
class Dataset(BaseDataset):
    """CamVid Dataset. Read images, apply augmentation and preprocessing transformations.
    
    Args:
        images_dir (str): path to images folder
        masks_dir (str): path to segmentation masks folder
        class_values (list): values of classes to extract from segmentation mask
        augmentation (albumentations.Compose): data transfromation pipeline 
            (e.g. flip, scale, etc.)
        preprocessing (albumentations.Compose): data preprocessing 
            (e.g. noralization, shape manipulation, etc.)
    
    """
    
    #CLASSES = ['0','1','2','3','4']
    
    def __init__(
            self, 
            filepaths, 
            augmentation=None, 
            #preprocessing=None,
    ):
        self.filepaths = filepaths
        self.augmentation = augmentation

    def __getitem__(self, i):
        # read data
        image_path = self.filepaths[i]
        image = cv.imread(image_path)
        image = cv.cvtColor(image, cv.COLOR_BGR2RGB)
        #print('Checking the class:')
        #print(os.path.normpath(image_path).split(os.sep)[-2])
        #Check the class:
        if os.path.normpath(image_path).split(os.sep)[-2]=='0':
            label = 0
        elif os.path.normpath(image_path).split(os.sep)[-2]=='1':
            label = 1
        elif os.path.normpath(image_path).split(os.sep)[-2]=='2':
            label = 2
        elif os.path.normpath(image_path).split(os.sep)[-2]=='3':
            label = 3
        elif os.path.normpath(image_path).split(os.sep)[-2]=='4':
            label = 4
        else:
            print('Something is wrong with the classes...')
        # apply augmentations
        if self.augmentation:
            image = self.augmentation(image=image)['image']    
        return image, label
        
    def __len__(self):
        return len(self.filepaths)


def train(model, dataloaders, optimizer, criterion, n_epochs): 

    best_model_wts = copy.deepcopy(model.state_dict())
    best_acc = 0.0

    for epoch_idx in range(n_epochs):

        for phase, dataloader in dataloaders.items():
            
            if phase == "TRAIN":
                model.train()
            else:
                model.eval()
            
            running_loss = 0.0
            running_acc = 0.0
            running_fscore = 0.0

            with torch.set_grad_enabled(phase == "TRAIN"):

                for i, (inputs, y_true) in enumerate(dataloader):
                    #print(inputs.shape)

                    inputs = inputs.to(DEVICE)
                    y_true = y_true.to(DEVICE)

                    y_pred = model(inputs)
                    #Will give the aux logits together with predictions when in training mode:
                    # https://pytorch.org/vision/0.9/_modules/torchvision/models/inception.html
                    #if phase == "TRAIN":
                    #    y_pred, aux_output = model(inputs)
                    #else:
                    #    y_pred = model(inputs)
                    
                    loss = criterion(y_pred, y_true)

                    if phase == "TRAIN":
                        optimizer.zero_grad()
                        loss.backward()
                        optimizer.step()
                    y_true = y_true.detach().cpu().numpy()
                    #Predict the most probable class:
                    y_pred = np.argmax(y_pred.detach().cpu().numpy(), axis=1)

                    #NB! Can consider to use balanced accuracy score instead!!!
                    running_loss += loss.item()
                    running_acc += metrics.accuracy_score(y_true, y_pred) 
                    running_fscore += metrics.f1_score(y_true, y_pred, average='macro')
            
            #print('Phase:',phase)
            #print('Length dataloader',len(dataloader))
            mean_loss = running_loss / len(dataloader)
            mean_acc = running_acc / len(dataloader)
            mean_fscore = running_fscore / len(dataloader)
            
            if phase == "VALID" and mean_acc > best_acc:
                best_acc = mean_acc
                best_model_wts = copy.deepcopy(model.state_dict())
            
            print("%s Epoch %i\t Loss: %.4f\t ACC: %.4f\t Mean F1: %.4f" % (phase, epoch_idx, mean_loss, mean_acc, mean_fscore))
    #The best model on the validation set 
    #after all epochs (total epochs) is saved:
    print("Best val Acc: %.4f" % best_acc)
    model.load_state_dict(best_model_wts)
    return model

def train_model(output_path, data_dir, n_classes, n_epochs=25):
 
    if not os.path.exists( output_path ):
        os.makedirs( output_path )

    model_save_path = os.path.join(output_path, "CroppedKaggle_Densenet121_100epochs.pt")
    #Pretrained network
    #If pretrained from scratch, aux_logits=True can help
    #https://discuss.pytorch.org/t/why-auxiliary-logits-set-to-false-in-train-mode/40705/4
    #To avoid the warning:
    #model = models.inception_v3(weights=models.Inception_V3_Weights.IMAGENET1K_V1)
    #Train and Densenet-121 instead...
    print('Loading the model...')
    model = models.densenet121(weights=models.DenseNet121_Weights.IMAGENET1K_V1)


    #How to extract intermediate layers from the model:
    #https://discuss.pytorch.org/t/how-can-i-extract-intermediate-layer-output-from-loaded-cnn-model/77301/2
    #model.fc = nn.Linear(model.fc.in_features, n_classes)
    #When Densenet121, the classification layer is called model.classifier, not model.fc
    model.classifier = nn.Linear(model.classifier.in_features, n_classes)

    model = model.to(DEVICE)
    
    optimizer = torch.optim.Adam(model.parameters()) 
    #Since multiclass, use crossEntropyLoss:
    criterion = nn.CrossEntropyLoss()

    #From Zoi: Use LR scheduler
    #scheduler = torch.optim.lr_scheduler.StepLR(optimizer, 10, gamma=0.1, last_epoch=-1)

    #February 13, after discussion with Josefine:
    #Remove centerCrop before training to avoid loss of information
    #Transformation from Zoi's code on fundus images:
    #Can try to compensate for the gap between original image size and new image size
    # https://stackoverflow.com/questions/72595995/how-to-train-network-on-images-of-different-sizes-pytorch
    transform_train_fund = transforms.Compose([transforms.Resize([620]), 
                                    transforms.RandomVerticalFlip(),
                                    transforms.RandomRotation(3),
                                    #transforms.CenterCrop([587]),
                                    transforms.ToTensor(),
                                    #Normalize to ImageNet...
                                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                                    transforms.RandomErasing()])
    
    transform_val_fund = transforms.Compose([transforms.Resize([620]),
                                    #transforms.CenterCrop([587]),
                                    transforms.ToTensor(),
                                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
    #Alternative augmentation
    #Also try to NOT resize the images (even though this might cause issues)
    #From line 1892: https://github.com/keras-team/keras/blob/v2.11.0/keras/preprocessing/image.py#L1166-L2144
    #contrast_range=0.2,
    #brightness_range=20.,
    #hue_range=10.,
    #saturation_range=20.,
    #blur_and_sharpen=True, #not defined in keras documentation
    #rotate_range=180.,
    #scale_range=0.2,
    #shear_range=0.2,
    #shift_range=0.2,
    #do_mirror=True, #Not defined in keras documentation
    #Also check this out:
    #https://www.kaggle.com/competitions/aptos2019-blindness-detection/discussion/107987
    transform_train_aptos = transforms.Compose([transforms.Resize([620]), 
                                    transforms.RandomVerticalFlip(p=0.5), # corresponds to 'do mirror'
                                    transforms.RandomHorizontalFlip(p=0.5),
                                    transforms.RandomRotation(180), #corresponds to 'RotateRange'
                                    transforms.ColorJitter(brightness=1,
                                    contrast=(0.8,1.2), saturation=1,hue=0.1), #Hue must lie between -0.5 and 0.5
                                    transforms.RandomAffine(degrees = 0,scale=(0.8,1.2),shear=0.2),
                                    transforms.RandomPerspective(distortion_scale=0.2,p=0.5),
                                    transforms.RandomApply(torch.nn.ModuleList([
                                        transforms.GaussianBlur(kernel_size = (7, 13))]),p=0.5),
                                    transforms.RandomResizedCrop((620,620), scale = (0.9, 1.0)), #Add some cropping and resize to (620,620)
                                    transforms.ToTensor(),
                                    #Normalize to ImageNet...
                                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
    transform_val_aptos = transforms.Compose([transforms.Resize([620]),
                                    transforms.ToTensor(),
                                    #Normalize to ImageNet...
                                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
    transform_train_clahe = albu.Compose([albu.CLAHE(clip_limit=2.0,p=1.0),
        albu.Resize(620,620),
        albu.HorizontalFlip(p=0.5),
        albu.VerticalFlip(p=0.5),
        albu.augmentations.geometric.rotate.RandomRotate90(),
        albu.ColorJitter (brightness=1, contrast=(0.8,1.2), saturation=1, hue=0.1, p=0.5),
        albu.Perspective(p=0.5),
        albu.AdvancedBlur(blur_limit=(7,13)),
        albu.augmentations.crops.transforms.RandomResizedCrop(620,620,scale = (0.9, 1.0),p=0.5),
        albu.Normalize(mean = (0.485, 0.456, 0.406),std=(0.229, 0.224, 0.225),p=1.0),
        ToTensorV2(),  
    ])

    transform_val_clahe = albu.Compose([albu.CLAHE(clip_limit=2.0,p=1.0),
        albu.Resize(620,620),
        albu.Normalize(mean = (0.485, 0.456, 0.406),std=(0.229, 0.224, 0.225),p=1.0),
        ToTensorV2()
    ])

    '''
    #NB! Must change the resize to avoid kernel size issues!
    # https://discuss.pytorch.org/t/kernel-size-cant-greater-than-actual-input-size/21579
    train_transforms = transforms.Compose([
        #transforms.Resize((224, 224)),
        transforms.Resize((620,620)),
        transforms.RandomRotation(degrees = (0,25)), # Add rotation
        #transforms.RandomResizedCrop((224,224), scale = (0.9, 1.0)), #Add some cropping, resize back to (224,224)
        transforms.RandomResizedCrop((620,620), scale = (0.9, 1.0)), #Add some cropping, resize back to (620,620)
        transforms.ToTensor(),
    ])
    
    valid_transforms = transforms.Compose([
        #transforms.Resize((224, 224)),
        transforms.Resize((620,620)),
        transforms.ToTensor(),
    ])
    '''
    #train_dataset = datasets.ImageFolder(os.path.join(data_dir, "CroppedTrainCombined"), transform_train_aptos)
    #valid_dataset = datasets.ImageFolder(os.path.join(data_dir, "CroppedValidCombined"), transform_val_aptos)

    #Create a customized dataset
    #See this link: https://albumentations.ai/docs/examples/pytorch_classification/
    train_folder = os.path.join(data_dir, "CroppedDataKaggle/CroppedTrainCombinedXL")
    small_list = [os.path.join(train_folder, str(class_id)) for class_id in range(n_classes)]
    print('Small list training:', small_list)
    train_filepath = []
    for _list in small_list:
        all_files = os.listdir(_list)
        print('Number of files:',len(all_files))
        all_paths = []
        #For each image in the class folder
        for _img in all_files:
            single_path = os.path.join(_list,_img)
            all_paths.append(single_path)
        #Add the full image path to image_list:
        train_filepath += all_paths
    print('Length of training files:',len(train_filepath))

    #Repeat for validation folder:
    valid_folder = os.path.join(data_dir, "CroppedDataKaggle/CroppedValidCombinedXL")
    small_listVal = [os.path.join(valid_folder, str(class_id)) for class_id in range(n_classes)]
    print('Small list validation:', small_listVal)
    valid_filepath = []
    for _list in small_listVal:
        all_files = os.listdir(_list)
        print('Number of files:',len(all_files))
        all_paths = []
        #For each image in the class folder
        for _img in all_files:
            single_path = os.path.join(_list,_img)
            all_paths.append(single_path)
        #Add the full image path to image_list:
        valid_filepath += all_paths
    print('Length of validation files:',len(valid_filepath))

    train_dataset = Dataset(train_filepath, augmentation = transform_train_clahe)
    valid_dataset = Dataset(valid_filepath, augmentation = transform_val_clahe)
    ########### Use the weighted random sampler #################
    #Expects a tensor weight for each sample
    #Inspired by this code: https://discuss.pytorch.org/t/how-to-handle-imbalanced-classes/11264/2
    #Get the number of observations for each class:
    class0_observations = len(os.listdir('Data/CroppedDataKaggle/CroppedTrainCombinedXL/0'))
    class1_observations = len(os.listdir('Data/CroppedDataKaggle/CroppedTrainCombinedXL/1'))
    class2_observations = len(os.listdir('Data/CroppedDataKaggle/CroppedTrainCombinedXL/2'))
    class3_observations = len(os.listdir('Data/CroppedDataKaggle/CroppedTrainCombinedXL/3'))
    class4_observations = len(os.listdir('Data/CroppedDataKaggle/CroppedTrainCombinedXL/4'))
    #class0_observations = len(os.listdir('Data/TrainCombined/0'))
    #class1_observations = len(os.listdir('Data/TrainCombined/1'))
    #class2_observations = len(os.listdir('Data/TrainCombined/2'))
    #class3_observations = len(os.listdir('Data/TrainCombined/3'))
    #class4_observations = len(os.listdir('Data/TrainCombined/4'))
    #print('Observations for class 0:', class0_observations)
    #print('Observations for class 1:', class1_observations)
    #print('Observations for class 2:', class2_observations)
    #print('Observations for class 3:', class3_observations)
    #print('Observations for class 4:', class4_observations)
    class_sample_count = np.array([class0_observations,class1_observations,class2_observations,class3_observations,class4_observations])
    class_weights = 1. / class_sample_count
    targets = [0,1,2,3,4]
    sample_weights = []
    for _t in targets:
        #Get X number of class weights, where X is number of obs for that given class
        sample_weigths_targetList = [class_weights[_t] for i in range(list(class_sample_count)[_t])]
        print('Weights for class',_t)
        print(sample_weigths_targetList[0])
        sample_weights +=  sample_weigths_targetList
    #The length of sample_weights must equal the total number of obs in training dataset:
    #print('Length of the sample weight list:',len(sample_weights))
    #print('Entire sample weight list:')
    #print(sample_weights[0])
    sample_weights = np.array(sample_weights)
    class_weights = torch.from_numpy(sample_weights)
    my_sampler = WeightedRandomSampler(class_weights,len(class_weights))    
    print('Length of class weights:',len(class_weights))

    #train_loader = DataLoader(train_dataset, batch_size=8, shuffle=True, num_workers=8) #Change to 8 workers due to warning
    #Add the weighted sampler (cannot use shuffle at the same time):
    # https://pytorch.org/docs/stable/data.html#torch.utils.data.DataLoader
    train_loader = DataLoader(train_dataset, batch_size=8, num_workers=8, sampler=my_sampler)
    valid_loader = DataLoader(valid_dataset, batch_size=1, shuffle=False, num_workers=4)
    print('Starting to train the model...')

    model = train(
        model=model,
        n_epochs=n_epochs,
        criterion=criterion,
        optimizer=optimizer,
        dataloaders={
            "TRAIN": train_loader,
            "VALID": valid_loader
        })
    
    #Save best model (based on validation set)
    torch.save(model.state_dict(), model_save_path)


if __name__ == "__main__":

    #args = argument_parser.parse_args()

    train_model(
        output_path = args.output_path,
        data_dir = args.data_path,
        n_classes = args.n_classes,
        n_epochs = args.epochs)