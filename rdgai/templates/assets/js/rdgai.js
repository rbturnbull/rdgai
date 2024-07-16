var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
  return new bootstrap.Tooltip(tooltipTriggerEl)
})
function relationBtnClick() {
    fetch("/api/relation-type", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
             "pair": this.dataset.pair ,
             "relation_type": this.dataset.relationtype,
             "operation": this.classList.contains("btn-primary") ? "remove" : "add"
        })
    })
    .then(response => {
        if (response.ok) {
            alert("ok");
            this.classList.toggle('btn-primary');
            this.classList.toggle('btn-secondary');
        } else {
            console.error('Failed to post data:', response.statusText);
        }
    })
    .catch(error => {
        console.error('Error:', error);
    });
}
document.querySelectorAll('.relation-btn').forEach(function(element) {
  element.addEventListener('click', relationBtnClick);
});
