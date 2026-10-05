import numpy as np
import keras

print("Keras :", keras.__version__)
modele = keras.models.load_model("modeles/unet_meilleur_modele.keras", compile=False)
print("Entrée attendue :", modele.input_shape)
sortie = modele.predict(np.zeros((1, 128, 128, 14), dtype="float32"))
print("Sortie obtenue  :", sortie.shape)