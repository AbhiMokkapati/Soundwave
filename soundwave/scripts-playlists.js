const inputTitle = document.getElementById("input-titles")
const inputArtist = document.getElementById("input-artists")
const inputGenre = document.getElementById("input-genres")
const inputReleaseYear = document.getElementById("input-releaseyears")

const submitPostReq = document.getElementById("submit-post-request")
const submitGetReq = document.getElementById("submit-get-request")

const list = document.getElementById("list")

submitPostReq.addEventListener("click", async function () {
     let data = {"title": inputTitle.value, "artist": inputArtist.value, "genre": inputGenre.value, "releaseyear": inputReleaseYear.value}
     await fetch("http://localhost:3000/new", {
        method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data),
    })



    inputTitle.value = ""
    inputArtist.value = ""
    inputGenre.value = ""
    inputReleaseYear.value = ""
});

submitGetReq.addEventListener("click", async function () {
    list.replaceChildren()
    const response = await fetch("http://localhost:3000/songs")
    const data = await response.json()
    for (let index = 0; index < data.length; index = index + 1) {
        const songTitle = data[index].title
        const songArtist = data[index].artist
        if (!songTitle){
            continue
        }
        const listElement = document.createElement("li")
        if (songArtist === undefined || songArtist === ""){
            listElement.textContent = songTitle
        } else {
            listElement.textContent = songTitle + " " + 'by' + " " + songArtist
        }


        listElement.className = 'getrequest'
        list.appendChild(listElement)
    }
});